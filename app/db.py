import json
import logging
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from .config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
SQLITE_INT_MAX = 2**63 - 1


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_week_start() -> str:
    d = datetime.now(timezone.utc).date()
    return d.fromisocalendar(d.isocalendar().year, d.isocalendar().week, 1).isoformat()


def connect():
    global _conn
    if _conn is not None:
        return _conn
    # Serialize creation: two threads racing an empty _conn would each build a
    # full pool (settings.database_pool_size connections against the shared
    # MariaDB max_connections) and leak one forever.
    with _lock:
        if _conn is not None:
            return _conn
        url = (getattr(settings, "database_url", "") or "").strip()
        if not url.startswith("mysql://"):
            raise RuntimeError("Fuse requires MariaDB: configure DATABASE_URL=mysql://…")
        from .mysql_compat import connect as mysql_connect
        conn = mysql_connect(url, max_connections=settings.database_pool_size)
        try:
            from .mysql_compat import SCHEMA_VERSION
            if not conn.execute("SELECT value FROM schema_meta WHERE key=?", (SCHEMA_VERSION,)).fetchone():
                raise RuntimeError("MariaDB schema has not been migrated and verified")
            conn.commit()
        except Exception:
            conn.close()
            raise
        _conn = conn
        return _conn


def close() -> None:
    global _conn
    with _lock:
        conn, _conn = _conn, None
        if conn is not None:
            conn.close()


def _init(conn: sqlite3.Connection) -> None:
    """Create and upgrade the base schema (app/db_schema.py)."""
    from .db_schema import _init as init

    init(conn)


def _ensure_column(conn, table: str, name: str, spec: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        spec = spec.replace("AUTOINCREMENT", "AUTO_INCREMENT")
        if (getattr(settings, "database_url", "") or "").startswith("mysql://") and spec.strip().upper().startswith("INTEGER"):
            spec = "BIGINT" + spec.strip()[7:]
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def _pget(price, key: str, default=None):
    """Read a pricing field from either an sqlite Row or a plain dict."""
    try:
        return price[key]
    except (KeyError, IndexError, TypeError):
        return default


def _canonical_output(row) -> tuple[int, int]:
    """(output, visible) with fallbacks for rows predating canonical fields."""
    output = int(row["output_tokens"] or 0)
    visible = int(row["visible_tokens"] or 0)
    if output <= 0:
        completion = int(row["completion_tokens"] or 0)
        reasoning = int(row["reasoning_tokens"] or 0)
        output = max(completion, reasoning)
        if visible <= 0:
            visible = max(completion - reasoning, 0)
    visible = min(visible, output)
    return output, visible


def _rollback(conn) -> None:
    """Roll back without masking the caller's exception.

    If the rollback itself fails the session state is unknown; close it so the
    next checkout reconnects instead of continuing an open transaction.
    """
    try:
        conn.rollback()
    except Exception:
        logger.exception("Database rollback failed; discarding the connection")
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def tx():
    conn = connect()
    pooled = hasattr(conn, "checkout")
    if pooled:
        borrowed = conn.checkout()
        try:
            yield borrowed
            borrowed.commit()
        except BaseException:
            # BaseException too: a GeneratorExit/KeyboardInterrupt must not
            # return a connection mid-transaction for the next borrower to
            # commit.
            _rollback(borrowed)
            raise
        finally:
            conn.release(borrowed)
        return
    with _lock:
        try:
            if hasattr(conn, "ensure_connected"):
                conn.ensure_connected()
            yield conn
            conn.commit()
        except BaseException:
            _rollback(conn)
            raise


def execute(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args)


def fetchone(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args).fetchone()


def fetchall(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args).fetchall()


def for_update(conn) -> str:
    """Row-lock suffix for a read-modify-write SELECT. MariaDB needs it (a
    plain read is a snapshot); legacy SQLite test connections reject the
    syntax and already hold the writer lock from BEGIN IMMEDIATE."""
    return "" if isinstance(conn, sqlite3.Connection) else " FOR UPDATE"


def md5_of(conn, column: str) -> str:
    """Select expression digesting a column inside MariaDB, so a writer can
    skip rewriting an identical multi-MB value without reading it back.
    Legacy SQLite test connections have no MD5(); NULL never matches there,
    so they always rewrite."""
    return "NULL" if isinstance(conn, sqlite3.Connection) else f"MD5({column})"


_provider_layout: tuple[object, bool] | None = None


def provider_split(conn=None) -> bool:
    """Whether chat_runs keeps the provider transcript as provider_base plus
    provider_tail behind a virtual provider_messages, so a save can write
    only the rows added since the base (scripts/sql/2026-10-20-*). Legacy
    SQLite test schemas keep the one plain column. Inside a transaction pass
    its conn: the first check must not wait for a second connection."""
    global _provider_layout
    pool = connect()
    if _provider_layout is None or _provider_layout[0] is not pool:
        if isinstance(pool, sqlite3.Connection):
            present = False
        else:
            rows = conn.execute("PRAGMA table_info(chat_runs)").fetchall() if conn is not None else fetchall("PRAGMA table_info(chat_runs)")
            present = any(column[1] == "provider_tail" for column in rows)
        _provider_layout = (pool, present)
    return _provider_layout[1]


def provider_column(conn=None) -> str:
    """Column that stores a whole provider transcript on INSERT."""
    return "provider_base" if provider_split(conn) else "provider_messages"


def set_provider_sql(conn=None) -> str:
    """SET fragment storing a whole provider transcript (one parameter)."""
    return "provider_base=?, provider_tail=NULL" if provider_split(conn) else "provider_messages=?"


def last_id(conn) -> int:
    sql = "SELECT last_insert_rowid()" if isinstance(conn, sqlite3.Connection) else "SELECT LAST_INSERT_ID()"
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] else 0


def record_usage(
    user_id: int,
    source_id: int | None,
    credential_id: int | None,
    model: str,
    usage: dict,
    at: str | None = None,
    *,
    key_id: int | None = None,
    provider: str = "",
    requested_model: str = "",
    request_status: str = "success",
    count_request: bool = True,
) -> None:
    def bounded(value) -> int:
        try:
            parsed = int(value or 0)
        except OverflowError:
            try:
                return SQLITE_INT_MAX if value > 0 else 0
            except (TypeError, ValueError):
                return 0
        except (TypeError, ValueError):
            return 0
        return min(max(parsed, 0), SQLITE_INT_MAX)

    def token(name: str) -> int:
        return bounded(usage.get(name))

    pt = token("prompt_tokens")
    ct = token("completion_tokens")
    rt = token("reasoning_tokens")
    output = token("output_tokens") or max(ct, rt)
    visible = min(token("visible_tokens") if "visible_tokens" in usage else max(output - rt, 0), output)
    cached = min(token("cached_tokens"), pt)
    cache_write = min(token("cache_write_tokens"), max(pt - cached, 0))
    audio = token("audio_tokens")
    image = token("image_tokens")
    total = _event_total(pt, output, token("total_tokens"))
    reported_raw = usage.get("cost_micros")
    reported_cost = bounded(reported_raw) if reported_raw is not None else None
    usage_status = str(usage.get("usage_status") or "unknown")
    if usage_status not in ("complete", "partial", "unknown"):
        usage_status = "unknown"
    request_status = request_status if request_status in ("success", "failed", "cancelled") else "failed"
    request_inc = 1 if count_request else 0
    ts = at or now()
    week = iso_week_start()
    model = model or ""
    provider = (provider or "")[:32]
    with tx() as conn:
        source = conn.execute("SELECT type, name FROM sources WHERE id=?", (source_id,)).fetchone() if source_id else None
        provider = provider or (source["type"] if source else "unknown")
        source_name = source["name"] if source else provider
        # OpenRouter is per-account only: every call is billed 1:1 on the
        # caller's own key (direct openrouter/... ids and pool-model fallback
        # alike), so tokens are counted but cost is not. Cost views must only
        # reflect what the shared pool/site accounts actually burn.
        track_cost = provider != "openrouter"
        price = (
            conn.execute(
                "SELECT * FROM model_prices WHERE provider=? AND model=?",
                (provider, model),
            ).fetchone()
            if track_cost
            else None
        )
        if reported_cost is not None and not track_cost:
            reported_cost = None
        estimated_cost = _estimated_cost(price, pt, output, visible, rt, cached, cache_write) if price else 0
        effective_cost = reported_cost if reported_cost is not None else estimated_cost
        conn.execute(
            """INSERT INTO usage_events
               (user_id, api_key_id, source_id, source_type, source_name, credential_id,
                 model, requested_model, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
                 reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens, total_tokens,
                  reported_cost_micros, estimated_cost_micros, pricing_known, usage_status, request_status,
                  count_request, provider_request_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, key_id, source_id, provider, source_name, credential_id,
                model, requested_model or model, pt, ct, output, visible, rt, cached, cache_write,
                 audio, image, total, reported_cost, estimated_cost, 1 if price else 0,
                 usage_status, request_status, request_inc,
                 str(usage.get("provider_request_id") or "")[:128], ts,
            ),
        )
        hour = ts[:13] + ":00:00Z"
        conn.execute(
            """INSERT INTO usage_hourly
               (user_id, hour, source_id, model, prompt_tokens, completion_tokens, output_tokens,
                visible_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens,
                cost_micros, requests)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, hour, source_id, model) DO UPDATE SET
                  prompt_tokens=MIN(9223372036854775807, prompt_tokens+excluded.prompt_tokens),
                   completion_tokens=MIN(9223372036854775807, completion_tokens+excluded.completion_tokens),
                   output_tokens=MIN(9223372036854775807, output_tokens+excluded.output_tokens),
                   visible_tokens=MIN(9223372036854775807, visible_tokens+excluded.visible_tokens),
                   reasoning_tokens=MIN(9223372036854775807, reasoning_tokens+excluded.reasoning_tokens),
                   cached_tokens=MIN(9223372036854775807, cached_tokens+excluded.cached_tokens),
                   cache_write_tokens=MIN(9223372036854775807, cache_write_tokens+excluded.cache_write_tokens),
                   total_tokens=MIN(9223372036854775807, total_tokens+excluded.total_tokens),
                   cost_micros=MIN(9223372036854775807, cost_micros+excluded.cost_micros),
                   requests=MIN(9223372036854775807, requests+excluded.requests)""",
            (user_id, hour, source_id or 0, model, pt, ct, output, visible, rt, cached,
             cache_write, total, effective_cost, request_inc),
        )
        if key_id:
            conn.execute(
                """UPDATE api_keys SET
                     prompt_tokens = MIN(9223372036854775807, prompt_tokens + ?),
                     completion_tokens = MIN(9223372036854775807, completion_tokens + ?),
                     output_tokens = MIN(9223372036854775807, output_tokens + ?),
                     visible_tokens = MIN(9223372036854775807, visible_tokens + ?),
                     reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + ?),
                     cached_tokens = MIN(9223372036854775807, cached_tokens + ?),
                     cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + ?),
                     audio_tokens = MIN(9223372036854775807, audio_tokens + ?),
                     image_tokens = MIN(9223372036854775807, image_tokens + ?),
                     total_tokens = MIN(9223372036854775807, total_tokens + ?),
                     requests = MIN(9223372036854775807, requests + ?),
                     cost_micros = MIN(9223372036854775807, cost_micros + ?),
                     last_model = ?,
                     usage_updated_at = ?
                   WHERE id=?""",
                (pt, ct, output, visible, rt, cached, cache_write, audio, image, total,
                 request_inc, effective_cost, model, ts, key_id),
            )
        if provider:
            conn.execute(
                """INSERT INTO provider_usage
                    (user_id, provider, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
                     reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens, total_tokens, requests,
                     cost_micros, last_model, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, provider) DO UPDATE SET
                      prompt_tokens = MIN(9223372036854775807, prompt_tokens + excluded.prompt_tokens),
                       completion_tokens = MIN(9223372036854775807, completion_tokens + excluded.completion_tokens),
                       output_tokens = MIN(9223372036854775807, output_tokens + excluded.output_tokens),
                       visible_tokens = MIN(9223372036854775807, visible_tokens + excluded.visible_tokens),
                       reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + excluded.reasoning_tokens),
                       cached_tokens = MIN(9223372036854775807, cached_tokens + excluded.cached_tokens),
                       cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + excluded.cache_write_tokens),
                      audio_tokens = MIN(9223372036854775807, audio_tokens + excluded.audio_tokens),
                      image_tokens = MIN(9223372036854775807, image_tokens + excluded.image_tokens),
                      total_tokens = MIN(9223372036854775807, total_tokens + excluded.total_tokens),
                       requests = MIN(9223372036854775807, requests + excluded.requests),
                      cost_micros = MIN(9223372036854775807, cost_micros + excluded.cost_micros),
                     last_model = excluded.last_model,
                     updated_at = excluded.updated_at""",
                (user_id, provider, pt, ct, output, visible, rt, cached, cache_write, audio,
                 image, total, request_inc, effective_cost, model, ts),
            )
        if credential_id:
            # One conditional UPDATE instead of a read-then-write: two requests
            # finishing on the same credential concurrently would otherwise lose
            # one increment (SQLite's writer lock had masked the race).
            # week_start is assigned last: MariaDB evaluates SET left to right,
            # so the CASEs must still see the previous week.
            conn.execute(
                """UPDATE credentials SET
                     week_tokens=CASE WHEN week_start=? THEN MIN(9223372036854775807, week_tokens+?) ELSE ? END,
                     week_requests=CASE WHEN week_start=? THEN MIN(9223372036854775807, week_requests+?) ELSE ? END,
                     week_start=?,
                     prompt_tokens = MIN(9223372036854775807, prompt_tokens + ?),
                     completion_tokens = MIN(9223372036854775807, completion_tokens + ?),
                     output_tokens = MIN(9223372036854775807, output_tokens + ?),
                     visible_tokens = MIN(9223372036854775807, visible_tokens + ?),
                     reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + ?),
                     cached_tokens = MIN(9223372036854775807, cached_tokens + ?),
                     cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + ?),
                     total_tokens = MIN(9223372036854775807, total_tokens + ?),
                     requests = MIN(9223372036854775807, requests + ?),
                     cost_micros = MIN(9223372036854775807, cost_micros + ?)
                   WHERE id=?""",
                (
                    week, total, total, week, request_inc, request_inc, week,
                    pt, ct, output, visible, rt, cached, cache_write, total, request_inc,
                    effective_cost, credential_id,
                ),
            )


def _event_total(prompt: int, output: int, total: int) -> int:
    # A provider total that leaves out reasoning (or anything else already in
    # prompt/output) would make totals disagree with the token breakdown.
    return min(max(total, prompt + output), SQLITE_INT_MAX)


def _estimated_cost(
    price, prompt: int, output: int, visible: int, reasoning: int, cached: int, cache_write: int
) -> int:
    cached = min(max(cached, 0), max(prompt, 0))
    cache_write = min(max(cache_write, 0), max(prompt - cached, 0))
    normal_input = max(prompt - cached - cache_write, 0)
    tiers = []
    raw_tiers = _pget(price, "tiers_json")
    if raw_tiers:
        try:
            tiers = json.loads(raw_tiers)
        except (TypeError, json.JSONDecodeError):
            tiers = []
    tier = None
    for candidate in tiers if isinstance(tiers, list) else []:
        if isinstance(candidate, dict) and int(candidate.get("min_prompt_tokens") or 0) <= prompt:
            if tier is None or int(candidate.get("min_prompt_tokens") or 0) > int(tier.get("min_prompt_tokens") or 0):
                tier = candidate
    threshold = _pget(price, "long_context_threshold")
    long_context = tier is None and bool(threshold and prompt >= int(threshold))
    input_rate = int(
        _pget(price, "long_input_micros_per_million")
        if long_context and _pget(price, "long_input_micros_per_million") is not None
        else _pget(price, "input_micros_per_million") or 0
    )
    output_rate = int(
        _pget(price, "long_output_micros_per_million")
        if long_context and _pget(price, "long_output_micros_per_million") is not None
        else _pget(price, "output_micros_per_million") or 0
    )
    cache_known = bool(_pget(price, "long_cached_input_known" if long_context else "cached_input_known"))
    cached_rate = int(
        _pget(price, "long_cached_input_micros_per_million")
        if long_context and cache_known
        else _pget(price, "cached_input_micros_per_million") if cache_known
        else input_rate
    )
    write_known = bool(_pget(price, "cache_write_known"))
    write_rate = int(_pget(price, "cache_write_micros_per_million") or 0) if write_known else input_rate
    if tier is not None:
        input_rate = int(tier.get("input_rate") if tier.get("input_rate") is not None else input_rate)
        output_rate = int(tier.get("output_rate") if tier.get("output_rate") is not None else output_rate)
        # A rate the tier leaves out keeps the published base rate. A base
        # rate that was never published means "bills as input", which in the
        # tier is the tier's input rate.
        if tier.get("cached_rate") is not None:
            cached_rate = int(tier["cached_rate"])
        elif not cache_known:
            cached_rate = input_rate
        if tier.get("cache_write_rate") is not None:
            write_rate = int(tier["cache_write_rate"])
        elif not write_known:
            write_rate = input_rate
    reasoning_known = bool(_pget(price, "reasoning_known"))
    reasoning_rate = int(_pget(price, "reasoning_micros_per_million") or 0)
    output = max(int(output or 0), 0)
    # Reasoning is part of output, never extra: overlap with the visible count
    # bills once, at the reasoning rate.
    reasoning = min(max(int(reasoning or 0), 0), output)
    numerator = (
        normal_input * input_rate
        + cached * cached_rate
        + cache_write * write_rate
        + (output * output_rate if not reasoning_known else (output - reasoning) * output_rate + reasoning * reasoning_rate)
    )
    return min(SQLITE_INT_MAX, max(0, (numerator + 500_000) // 1_000_000))


def save_model_price(
    provider: str,
    model: str,
    input_rate: int,
    cached_rate: int | None,
    output_rate: int,
    reasoning_rate: int | None,
    *,
    cache_write_rate: int | None = None,
    tiers: list[dict] | None = None,
    long_context_threshold: int | None = None,
    long_input_rate: int | None = None,
    long_cached_rate: int | None = None,
    long_output_rate: int | None = None,
    origin: str = "manual",
    openrouter_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert a price row and reprice matching estimated history.

    Manual rows win: an 'openrouter'-origin sync never overwrites a row whose
    current origin is 'manual'. The dashboard documents that contract.
    """
    ts = now()
    tiers_json = json.dumps(
        [
            {
                "min_prompt_tokens": int(t.get("min_prompt_tokens") or 0),
                "input_rate": t.get("input_rate"),
                "cached_rate": t.get("cached_rate"),
                "cache_write_rate": t.get("cache_write_rate"),
                "output_rate": t.get("output_rate"),
            }
            for t in (tiers or [])
            if isinstance(t, dict) and int(t.get("min_prompt_tokens") or 0) > 0
        ]
    )
    transaction = tx() if conn is None else nullcontext(conn)
    with transaction as write_conn:
        write_conn.execute(
            """INSERT INTO model_prices
               (provider, model, input_micros_per_million, cached_input_micros_per_million,
                cache_write_micros_per_million, output_micros_per_million, reasoning_micros_per_million,
                cached_input_known, cache_write_known, reasoning_known, long_context_threshold,
                long_input_micros_per_million, long_cached_input_micros_per_million,
                long_output_micros_per_million, long_cached_input_known, tiers_json,
                origin, openrouter_id, synced_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider, model) DO UPDATE SET
                  input_micros_per_million=excluded.input_micros_per_million,
                  cached_input_micros_per_million=excluded.cached_input_micros_per_million,
                  cache_write_micros_per_million=excluded.cache_write_micros_per_million,
                  output_micros_per_million=excluded.output_micros_per_million,
                  reasoning_micros_per_million=excluded.reasoning_micros_per_million,
                  cached_input_known=excluded.cached_input_known,
                  cache_write_known=excluded.cache_write_known,
                  reasoning_known=excluded.reasoning_known,
                  long_context_threshold=excluded.long_context_threshold,
                  long_input_micros_per_million=excluded.long_input_micros_per_million,
                  long_cached_input_micros_per_million=excluded.long_cached_input_micros_per_million,
                  long_output_micros_per_million=excluded.long_output_micros_per_million,
                  long_cached_input_known=excluded.long_cached_input_known,
                  tiers_json=excluded.tiers_json,
                  origin=excluded.origin,
                  openrouter_id=excluded.openrouter_id,
                  synced_at=excluded.synced_at,
                  updated_at=excluded.updated_at
               WHERE model_prices.origin != 'manual' OR excluded.origin = 'manual'""",
            (
                provider, model, input_rate, cached_rate or 0, cache_write_rate or 0,
                output_rate, reasoning_rate or 0,
                1 if cached_rate is not None else 0, 1 if cache_write_rate is not None else 0,
                1 if reasoning_rate is not None else 0,
                long_context_threshold, long_input_rate, long_cached_rate, long_output_rate,
                1 if long_cached_rate is not None else 0, tiers_json,
                origin[:32], openrouter_id, ts if origin == "openrouter" else None, ts,
            ),
        )
        persisted_price = write_conn.execute(
            "SELECT * FROM model_prices WHERE provider=? AND model=?",
            (provider, model),
        ).fetchone()
        if persisted_price is None:
            return
        if provider == "openrouter":
            # The price row is kept for reference, but OpenRouter events are
            # personal (per-account, user pays 1:1): repricing history must
            # never attach estimated cost to them.
            return
        for rows in _priced_events(
            write_conn,
            """e.id, e.user_id, e.api_key_id, e.credential_id, e.reported_cost_micros,
               e.estimated_cost_micros, e.prompt_tokens, e.completion_tokens,
               e.output_tokens, e.visible_tokens, e.reasoning_tokens, e.cached_tokens,
               e.cache_write_tokens, e.source_id, e.created_at""",
            provider,
            model,
        ):
            _reprice_batch(write_conn, provider, model, persisted_price, rows)


# Events repriced per id window: a popular model's whole history never sits in
# memory at once. Windows on the primary key keep each query a bounded range
# scan; ORDER BY id LIMIT re-sorts the model's remaining rows on every page.
REPRICE_WINDOW = 5000


def _priced_events(conn, columns: str, provider: str, model: str):
    span = conn.execute("SELECT MIN(id) AS lo, MAX(id) AS hi FROM usage_events WHERE model=?", (model,)).fetchone()
    if span is None or span["lo"] is None:
        return
    lo, hi = int(span["lo"]) - 1, int(span["hi"])
    while lo < hi:
        rows = conn.execute(
            f"""SELECT {columns}
                FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
                WHERE COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')=? AND e.model=?
                  AND e.id>? AND e.id<=?""",
            (provider, model, lo, lo + REPRICE_WINDOW),
        ).fetchall()
        lo += REPRICE_WINDOW
        if rows:
            yield rows


def _reprice_batch(write_conn, provider, model, persisted_price, rows) -> None:
    updates = []
    key_deltas: dict[int, int] = {}
    user_deltas: dict[int, int] = {}
    cred_deltas: dict[int, int] = {}
    hour_deltas: dict[tuple, int] = {}
    for row in rows:
        output, visible = _canonical_output(row)
        estimate = _estimated_cost(
            persisted_price,
            int(row["prompt_tokens"] or 0),
            output,
            visible,
            int(row["reasoning_tokens"] or 0),
            int(row["cached_tokens"] or 0),
            int(row["cache_write_tokens"] or 0),
        )
        old_effective = row["reported_cost_micros"] if row["reported_cost_micros"] is not None else int(row["estimated_cost_micros"] or 0)
        new_effective = row["reported_cost_micros"] if row["reported_cost_micros"] is not None else estimate
        delta = int(new_effective) - int(old_effective)
        updates.append((estimate, row["id"]))
        user_deltas[row["user_id"]] = user_deltas.get(row["user_id"], 0) + delta
        if row["api_key_id"]:
            key_deltas[row["api_key_id"]] = key_deltas.get(row["api_key_id"], 0) + delta
        if row["credential_id"]:
            cred_deltas[row["credential_id"]] = cred_deltas.get(row["credential_id"], 0) + delta
        hour = _hour_key(row)
        hour_deltas[hour] = hour_deltas.get(hour, 0) + delta
    write_conn.executemany(
        "UPDATE usage_events SET estimated_cost_micros=?, pricing_known=1 WHERE id=?",
        updates,
    )
    _apply_cost_deltas(write_conn, provider, model, key_deltas, user_deltas, cred_deltas, hour_deltas)


def _hour_key(row) -> tuple:
    return (row["user_id"], str(row["created_at"])[:13] + ":00:00Z", int(row["source_id"] or 0))


def _apply_cost_deltas(conn, provider, model, key_deltas, user_deltas, cred_deltas, hour_deltas) -> None:
    """Move every cost rollup by exactly what its events moved. No floor at
    zero: a clamp would silently break rollup == SUM(events) for good."""
    conn.executemany(
        "UPDATE api_keys SET cost_micros=cost_micros+? WHERE id=?",
        [(delta, key_id) for key_id, delta in key_deltas.items() if delta],
    )
    conn.executemany(
        "UPDATE provider_usage SET cost_micros=cost_micros+? WHERE user_id=? AND provider=?",
        [(delta, user_id, provider) for user_id, delta in user_deltas.items() if delta],
    )
    conn.executemany(
        "UPDATE credentials SET cost_micros=cost_micros+? WHERE id=?",
        [(delta, cred_id) for cred_id, delta in cred_deltas.items() if delta],
    )
    conn.executemany(
        "UPDATE usage_hourly SET cost_micros=cost_micros+? WHERE user_id=? AND hour=? AND source_id=? AND model=?",
        [(delta, user_id, hour, source_id, model) for (user_id, hour, source_id), delta in hour_deltas.items() if delta],
    )


def delete_model_price(provider: str, model: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM model_prices WHERE provider=? AND model=?", (provider, model))
        for rows in _priced_events(
            conn,
            """e.id, e.user_id, e.api_key_id, e.credential_id, e.reported_cost_micros, e.estimated_cost_micros,
               e.source_id, e.created_at""",
            provider,
            model,
        ):
            _unprice_batch(conn, provider, model, rows)


def _unprice_batch(conn, provider, model, rows) -> None:
    conn.executemany(
        "UPDATE usage_events SET estimated_cost_micros=0, pricing_known=0 WHERE id=?",
        [(row["id"],) for row in rows],
    )
    key_deltas: dict[int, int] = {}
    user_deltas: dict[int, int] = {}
    cred_deltas: dict[int, int] = {}
    hour_deltas: dict[tuple, int] = {}
    for row in rows:
        if row["reported_cost_micros"] is not None:
            continue
        delta = -int(row["estimated_cost_micros"] or 0)
        user_deltas[row["user_id"]] = user_deltas.get(row["user_id"], 0) + delta
        if row["api_key_id"]:
            key_deltas[row["api_key_id"]] = key_deltas.get(row["api_key_id"], 0) + delta
        if row["credential_id"]:
            cred_deltas[row["credential_id"]] = cred_deltas.get(row["credential_id"], 0) + delta
        hour = _hour_key(row)
        hour_deltas[hour] = hour_deltas.get(hour, 0) + delta
    _apply_cost_deltas(conn, provider, model, key_deltas, user_deltas, cred_deltas, hour_deltas)


def provider_usage_for(user_id: int):
    return fetchall(
        "SELECT * FROM provider_usage WHERE user_id=? ORDER BY total_tokens DESC, provider",
        (user_id,),
    )
