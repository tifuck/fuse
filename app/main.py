import asyncio
import inspect
import logging
import re
import fcntl
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from .config import ROOT, settings
from . import accounts, db, chat_store, chat_runs, chat_events, chat_recaps, chat_titles, host_events, issues_pipeline, retention, sources as sources_module
from .api import router as api_router
from .web import router as web_router
from .agent_http import router as agent_http_router
from .sources import bootstrap, start_pools
from .sources.chatgpt import refresh_due as chatgpt_refresh_due
from .sources.grok import refresh_due
from .sources.grok_version import refresh_due as grok_version_refresh_due
from .sources.kimi import refresh_due as kimi_refresh_due
from .sources.claude import refresh_due as claude_refresh_due
from .sources.openrouter import refresh_due as openrouter_refresh_due
from .pricing import auto_price_due
from .sources.usage_walk import WALK_PAUSE, walk_next
from .security import MaxBodyMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)
_REFRESH_INTERVAL = 60
_REFRESH_MAX_BACKOFF = 300


async def _maintenance_loop(app: FastAPI):
    """Independent watchdog for run-state recovery: never shares a cadence,
    backoff, or failure domain with the external refresh jobs."""
    while True:
        await asyncio.sleep(_REFRESH_INTERVAL)
        try:
            chat_store.prune_events()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat event prune failed")
        try:
            chat_runs.reap_orphans()
            await chat_runs.resume_queued()
            chat_runs.sweep_wakes()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat run reaper failed")
        try:
            await retention.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat retention sweep failed")


async def _run_refresh_job(name: str, job) -> str | None:
    try:
        await asyncio.wait_for(job(), timeout=120)
        return None
    except asyncio.TimeoutError:
        logger.error("Background refresh timed out for %s", name)
        return f"{name}: timeout"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Background refresh failed for %s", name)
        return f"{name}: {type(exc).__name__}"


async def _usage_walk_loop(app: FastAPI):
    """One paid account at a time, oldest-checked first, with a pause between hops."""
    while True:
        try:
            await walk_next()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Usage walk failed")
            app.state.walk_healthy = False
            app.state.walk_errors = ["usage walk: failed"]
        else:
            app.state.walk_healthy = True
            app.state.walk_errors = []
            app.state.refresh_last_cycle = db.now()
        await asyncio.sleep(WALK_PAUSE)


async def _refresh_loop(app: FastAPI):
    provider_jobs = (
        ("grok client version", grok_version_refresh_due),
        ("grok credentials", refresh_due),
        ("chatgpt credentials", chatgpt_refresh_due),
        ("kimi credentials", kimi_refresh_due),
        ("claude credentials", claude_refresh_due),
        ("openrouter catalog", openrouter_refresh_due),
    )
    local_jobs = (
        ("model pricing", auto_price_due),
        ("bug triage", issues_pipeline.tick),
    )
    delay = _REFRESH_INTERVAL
    while True:
        await asyncio.sleep(delay)
        failures: list[str] = []
        try:
            results = await asyncio.gather(
                *(_run_refresh_job(name, job) for name, job in provider_jobs),
            )
            failures.extend(err for err in results if err)
            for name, job in local_jobs:
                err = await _run_refresh_job(name, job)
                if err:
                    failures.append(err)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background refresh cycle failed")
        if failures:
            app.state.refresh_errors = failures
            app.state.refresh_healthy = False
        else:
            app.state.refresh_errors = []
            app.state.refresh_healthy = True
        delay = min(_REFRESH_MAX_BACKOFF, delay * 2) if failures else _REFRESH_INTERVAL


def _refresh_done(app: FastAPI, task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    app.state.refresh_healthy = False
    app.state.refresh_errors = [f"refresh task stopped: {type(exc).__name__ if exc else 'unknown'}"]
    if exc:
        logger.error("Background refresh task stopped", exc_info=(type(exc), exc, exc.__traceback__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    maint_task = None
    walk_task = None
    worker_lock = None
    # asyncio's default executor is min(32, cpu+4) threads (8 on a 4-core
    # host): every off-loop DB call, checkpoint, and socket replay shares it.
    # Size it to the database pool instead so a saturated pool queues in
    # worker threads instead of serializing the whole process.
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(
            max_workers=max(8, settings.database_pool_size),
            thread_name_prefix="fuse-io",
        )
    )
    try:
        url = (getattr(settings, "database_url", "") or "").strip()
        if settings.database_mariadb_enabled and url.startswith("mysql://"):
            schema = urlsplit(url).path.strip("/") or "fuse"
            if not re.fullmatch(r"[A-Za-z0-9_-]+", schema):
                raise RuntimeError("Refusing worker lock for an invalid database name")
            lock_dir = ROOT / "data"
            lock_dir.mkdir(parents=True, exist_ok=True)
            worker_lock = open(lock_dir / f"fuse.{schema}.chat-worker.lock", "a")
        else:
            raise RuntimeError("Fuse requires a configured MariaDB database")
        if worker_lock is not None:
            try:
                fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Dashboard runs require one Fuse worker per database.") from None
        db.connect()
        accounts.validate_credential_encryption()
        chat_store.recover_runs()
        from . import command_jobs

        command_jobs.recover()
        from . import skill_install

        recovered_skills = skill_install.sweep_partial_swaps()
        if recovered_skills:
            logger.warning("Recovered skill directories from partial swaps: %s", recovered_skills)
        from .issues_pipeline import recover_stuck as recover_stuck_issues

        stuck_reports = recover_stuck_issues()
        if stuck_reports:
            logger.warning("Released %d bug reports stuck in triaging", stuck_reports)
        chat_runs._stopping = False
        chat_events.start()
        host_events.start()
        chat_store.start_publisher()
        await bootstrap()
        start_pools()
        await chat_runs.resume_queued()
        chat_runs.sweep_wakes()
        from . import schedules as schedules_mod

        schedules_mod.recover()
        schedules_mod.reconcile()
        sched_task = asyncio.create_task(schedules_mod.loop(app), name="fuse-schedules")
        app.state.schedules_task = sched_task
        app.state.refresh_healthy = True
        app.state.refresh_errors = []
        app.state.refresh_last_cycle = None
        app.state.walk_healthy = True
        app.state.walk_errors = []
        task = asyncio.create_task(_refresh_loop(app), name="fuse-refresh")
        task.add_done_callback(lambda done: _refresh_done(app, done))
        app.state.refresh_task = task
        walk_task = asyncio.create_task(_usage_walk_loop(app), name="fuse-usage-walk")
        walk_task.add_done_callback(lambda done: _refresh_done(app, done))
        app.state.usage_walk_task = walk_task
        maint_task = asyncio.create_task(_maintenance_loop(app), name="fuse-maintenance")
        app.state.maintenance_task = maint_task
        recap_task = asyncio.create_task(chat_recaps.loop(), name="fuse-recaps")
        title_task = asyncio.create_task(chat_titles.loop(), name="fuse-titles")
        from .search.client import start_client
        from .search.engine import warn_if_unkeyed

        app.state.search_client = start_client()
        warn_if_unkeyed()
        yield
    finally:
        await chat_events.stop()
        await host_events.stop()
        if "title_task" in locals():
            title_task.cancel()
            await asyncio.gather(title_task, return_exceptions=True)
        await chat_titles.drain()
        if "recap_task" in locals():
            recap_task.cancel()
            await asyncio.gather(recap_task, return_exceptions=True)
        await chat_recaps.drain()
        await chat_runs.stop()
        # After the runs drain: flush any publications still queued so their
        # waiters resolve and terminal events are not lost mid-shutdown.
        await chat_store.stop_publisher()
        try:
            from . import ssh_pool, vault, agent_hub
            await agent_hub.drain()
            await ssh_pool.drain()
            vault.lock_all()
        except Exception:
            logger.exception("Failed while draining ssh vault")
        try:
            from .search.client import stop_client

            await stop_client()
        except Exception:
            logger.exception("Failed while closing search HTTP client")
        try:
            sched_task = getattr(app.state, "schedules_task", None)
            if sched_task is not None:
                sched_task.cancel()
                try:
                    await sched_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Failed while awaiting schedule loop shutdown")
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Failed while awaiting background refresh shutdown")
            if walk_task is not None:
                walk_task.cancel()
                try:
                    await walk_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Failed while awaiting usage walk shutdown")
            if maint_task is not None:
                maint_task.cancel()
                try:
                    await maint_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Failed while awaiting maintenance loop shutdown")
        finally:
            try:
                stop_pools = getattr(sources_module, "stop_pools", None)
                if callable(stop_pools):
                    result = stop_pools()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                logger.exception("Failed while stopping background pools")
            finally:
                db.close()
                if worker_lock is not None:
                    worker_lock.close()


app = FastAPI(title="Fuse", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=3)
app.add_middleware(MaxBodyMiddleware)
hosts = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()]
if hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret,
    same_site="lax",
    https_only=True,
    max_age=7 * 24 * 60 * 60,
)
_VERSIONED = re.compile(rb"(?:^|&)v=[^&]")


class VersionedStaticFiles(StaticFiles):
    """Versioned assets (?v=N, raised on every change by promote-dev.sh) are
    cached without revalidation; unversioned ones keep the default validators."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if settings.static_immutable and response.status_code == 200 and _VERSIONED.search(scope.get("query_string", b"")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", VersionedStaticFiles(directory=str(ROOT / "static")), name="static")
app.mount("/desktop", StaticFiles(directory=str(ROOT / "data" / "desktop-releases")), name="desktop")
app.include_router(api_router)
app.include_router(web_router)
app.include_router(chat_events.router)
app.include_router(host_events.router)
app.include_router(agent_http_router)


@app.get("/v1/health")
async def health(request: Request):
    try:
        row = await asyncio.to_thread(db.fetchone, "SELECT 1 AS ok")
        database_ok = bool(row and row["ok"] == 1)
    except Exception:
        database_ok = False
    task = getattr(app.state, "refresh_task", None)
    maint = getattr(app.state, "maintenance_task", None)
    walk = getattr(app.state, "usage_walk_task", None)
    task_running = bool(task and not task.done()) and bool(maint and not maint.done()) and bool(walk and not walk.done())
    refresh_ok = bool(getattr(app.state, "refresh_healthy", False))
    walk_ok = bool(getattr(app.state, "walk_healthy", True))
    healthy = bool(database_ok and refresh_ok and walk_ok and task_running)
    errors = list(getattr(app.state, "refresh_errors", [])) + list(getattr(app.state, "walk_errors", []))
    background = {
        "running": task_running,
        "healthy": refresh_ok and walk_ok,
        "last_cycle": getattr(app.state, "refresh_last_cycle", None),
    }
    # Failure detail (exception types, job names) is for signed-in operators;
    # anonymous callers get a count so the health contract stays stable.
    if request.session.get("uid"):
        background["errors"] = errors
    else:
        background["error_count"] = len(errors)
    payload = {
        "ok": healthy,
        "database": {"ok": database_ok, "backend": "mariadb"},
        "background": background,
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)
