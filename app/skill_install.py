"""Disk persist, zip/md upload, skills.sh search, GitHub zipball install."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse

from .config import ROOT, settings
from .search.ssrf import ips_public, public_http_url
from .security import limiter
from .skill_parse import NAME_RE, SkillError, parse_skill_md

logger = logging.getLogger("app.skills")

ALLOWED_EXT = {
    ".md", ".txt", ".py", ".js", ".ts", ".sh", ".json", ".yml", ".yaml",
    ".toml", ".html", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_UNCOMPRESSED = 1 * 1024 * 1024
MAX_FILES = 50
MAX_DEPTH = 4
MAX_ZIP = 1 * 1024 * 1024
MAX_ZIPBALL = 8 * 1024 * 1024
MAX_FILE = 256 * 1024
_SEG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_GH_HOSTS = {"github.com", "api.github.com", "raw.githubusercontent.com", "codeload.github.com"}
_SKILLS_HOSTS = {"skills.sh", "www.skills.sh"}
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 45.0
_CACHE_MAX = 200
LAYOUTS = (
    "skills/{id}/SKILL.md",
    "{id}/SKILL.md",
    ".claude/skills/{id}/SKILL.md",
    ".agents/skills/{id}/SKILL.md",
    ".opencode/skills/{id}/SKILL.md",
    "SKILL.md",
)


def data_dir() -> Path:
    raw = (settings.database_path or "").strip()
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        path = ROOT / "data" / "skills"
    else:
        path = Path(raw).expanduser().resolve().parent / "skills"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def user_dir(user_id: int) -> Path:
    path = data_dir() / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def skill_dir(user_id: int, slug: str) -> Path:
    return user_dir(user_id) / slug


def valid_github_source(source: str) -> bool:
    if not isinstance(source, str) or source.count("/") != 1:
        return False
    owner, repo = source.split("/")
    for part in (owner, repo):
        if part in (".", "..") or part.startswith(".") or not _SEG.match(part):
            return False
    return True


def content_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path])
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_rel(path: str, *, max_depth: int = MAX_DEPTH) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        raise SkillError("invalid skill path")
    if "\x00" in raw or ".." in Path(raw).parts:
        raise SkillError("invalid skill path")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        raise SkillError("invalid skill path")
    if len(parts) - 1 > max_depth:
        raise SkillError("skill tree too deep")
    return "/".join(parts)


def validate_files(files: dict[str, bytes]) -> None:
    if not files or "SKILL.md" not in files:
        raise SkillError("SKILL.md is required")
    if len(files) > MAX_FILES:
        raise SkillError("too many files")
    total = 0
    for rel, blob in files.items():
        _safe_rel(rel)
        if not isinstance(blob, (bytes, bytearray)):
            raise SkillError("invalid skill file")
        if len(blob) > MAX_FILE:
            raise SkillError("skill file too large")
        total += len(blob)
        ext = Path(rel).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise SkillError(f"disallowed file type: {rel}")
    if total > MAX_UNCOMPRESSED:
        raise SkillError("skill too large")


def persist_tree(user_id: int, slug: str, files: dict[str, bytes]) -> str:
    """Atomic replace of data/skills/{uid}/{slug}/. Returns content_hash."""
    if not NAME_RE.fullmatch(slug or ""):
        raise SkillError("invalid skill name")
    validate_files(files)
    digest = content_hash(files)
    parent = user_dir(user_id)
    dest = parent / slug
    nonce = secrets.token_hex(8)
    tmp = parent / f".tmp-{slug}-{nonce}"
    old = parent / f".old-{slug}-{nonce}"
    moved_aside = False
    try:
        tmp.mkdir(mode=0o700)
        for rel, blob in files.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        dir_fd = os.open(tmp, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        if dest.exists():
            dest.rename(old)
            moved_aside = True
        tmp.rename(dest)
    except SkillError:
        shutil.rmtree(tmp, ignore_errors=True)
        if moved_aside and not dest.exists():
            _restore_aside(old, dest)
        raise
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if moved_aside and not dest.exists():
            _restore_aside(old, dest)
        logger.warning("persist_tree failed slug=%s: %s", slug, type(exc).__name__)
        raise SkillError("could not store skill") from exc
    # Only now is the swap complete: the previous version is expendable.
    shutil.rmtree(old, ignore_errors=True)
    return digest


def _restore_aside(old: Path, dest: Path) -> None:
    """Put the only remaining copy of the previous version back in place."""
    try:
        old.rename(dest)
    except Exception:
        logger.exception(
            "Could not restore previous skill copy from %s to %s; the data is preserved at the .old- path",
            old,
            dest,
        )


def sweep_partial_swaps(base_dir=None) -> list[str]:
    """Recover from a crash mid-swap: restore the newest .old-* when the live
    dir is missing, and clear stale .tmp-* staging dirs. Returns recovered slugs."""
    root = Path(base_dir) if base_dir else data_dir()
    recovered: list[str] = []
    if not root.is_dir():
        return recovered
    for user_path in root.iterdir():
        if not user_path.is_dir():
            continue
        by_slug: dict[str, list[Path]] = {}
        for entry in user_path.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith(".tmp-"):
                shutil.rmtree(entry, ignore_errors=True)
            elif name.startswith(".old-"):
                by_slug.setdefault(name[len(".old-"):].rsplit("-", 1)[0], []).append(entry)
        for slug, olds in by_slug.items():
            if not (user_path / slug).exists():
                newest = max(olds, key=lambda p: p.stat().st_mtime)
                _restore_aside(newest, user_path / slug)
                recovered.append(f"{user_path.name}/{slug}")
                olds.remove(newest)
            for stale in olds:
                shutil.rmtree(stale, ignore_errors=True)
    return recovered


def remove_tree(user_id: int, slug: str) -> None:
    path = skill_dir(user_id, slug)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def files_from_zip(data: bytes, *, max_compressed: int = MAX_ZIP, loose: bool = False) -> dict[str, bytes]:
    if not data or len(data) > max_compressed:
        raise SkillError("zip too large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("invalid zip") from exc
    total = 0
    raw: dict[str, bytes] = {}
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/") or info.is_dir():
            continue
        if _zip_is_symlink(info):
            if loose:
                continue
            raise SkillError("symlinks are not allowed")
        if info.file_size > MAX_FILE:
            if loose:
                continue
            raise SkillError("skill file too large")
        if not loose:
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise SkillError("skill too large")
            rel = _safe_rel(name)
        else:
            try:
                rel = _safe_rel(name, max_depth=16)
            except SkillError:
                continue
        raw[rel] = zf.read(info)
    if not raw:
        raise SkillError("zip is empty")
    return raw


def _skill_md_paths(files: dict[str, bytes]) -> list[str]:
    return [p for p in files if Path(p).name == "SKILL.md"]


def slice_skill_root(files: dict[str, bytes], root_md: str) -> dict[str, bytes]:
    parent = str(Path(root_md).parent).replace("\\", "/")
    sliced: dict[str, bytes] = {}
    if parent in (".", ""):
        for path, blob in files.items():
            if path == root_md:
                sliced["SKILL.md"] = blob
            elif "/" not in path:
                sliced[path] = blob
    else:
        prefix = parent.rstrip("/") + "/"
        for path, blob in files.items():
            if path == root_md:
                sliced["SKILL.md"] = blob
            elif path.startswith(prefix):
                rel = path[len(prefix) :]
                if rel:
                    sliced[rel] = blob
    if "SKILL.md" not in sliced:
        raise SkillError("SKILL.md is required")
    return sliced


def _decode_md(blob: bytes) -> str:
    return blob.decode("utf-8-sig")


def filter_skill_files(files: dict[str, bytes]) -> dict[str, bytes]:
    """Keep allowlisted files under the skill root. SKILL.md is required."""
    out: dict[str, bytes] = {}
    for rel, blob in files.items():
        try:
            safe = _safe_rel(rel)
        except SkillError:
            continue
        if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_FILE:
            continue
        if Path(safe).suffix.lower() not in ALLOWED_EXT:
            continue
        out[safe] = bytes(blob)
    if "SKILL.md" not in out:
        raise SkillError("SKILL.md is required")
    if len(out) > MAX_FILES:
        keep = {"SKILL.md": out["SKILL.md"]}
        for path in sorted(out):
            if path == "SKILL.md":
                continue
            keep[path] = out[path]
            if len(keep) >= MAX_FILES:
                break
        out = keep
    total = sum(len(b) for b in out.values())
    if total > MAX_UNCOMPRESSED:
        out = {"SKILL.md": out["SKILL.md"]}
    return out


def extract_skill_files(files: dict[str, bytes], *, skill_id: str | None = None) -> tuple[str, dict[str, bytes]]:
    """Find a SKILL.md root. Returns (slug, files-relative-to-root)."""
    md_paths = _skill_md_paths(files)
    if not md_paths:
        raise SkillError("SKILL.md is required")
    chosen = None
    if skill_id:
        for layout in LAYOUTS:
            candidate = layout.format(id=skill_id)
            if candidate in files:
                chosen = candidate
                break
        if chosen is None:
            matches = []
            for path in md_paths:
                try:
                    parsed = parse_skill_md(_decode_md(files[path]), slug=skill_id)
                except (SkillError, UnicodeDecodeError):
                    continue
                if parsed["name"] == skill_id:
                    matches.append(path)
            if matches:
                matches.sort(key=lambda p: p.count("/"))
                chosen = matches[0]
        if chosen is None:
            # Last resort: any SKILL.md whose folder name is the skill id.
            for path in sorted(md_paths, key=lambda p: p.count("/")):
                if Path(path).parent.name == skill_id:
                    chosen = path
                    break
        if chosen is None:
            raise SkillError("Could not find SKILL.md")
    else:
        if len(md_paths) != 1:
            raise SkillError("zip must contain exactly one SKILL.md")
        chosen = md_paths[0]
    sliced = filter_skill_files(slice_skill_root(files, chosen))
    parsed = parse_skill_md(_decode_md(sliced["SKILL.md"]), slug=skill_id)
    return parsed["name"], sliced


def parse_upload(filename: str, data: bytes) -> tuple[str, dict[str, bytes]]:
    name = (filename or "").lower()
    if name.endswith(".zip"):
        raw = files_from_zip(data, max_compressed=MAX_ZIP)
        return extract_skill_files(raw)
    if name.endswith(".md") or (filename or "") in ("SKILL.md", "skill.md"):
        text = data.decode("utf-8-sig")
        parsed = parse_skill_md(text)
        files = {"SKILL.md": text.encode("utf-8")}
        validate_files(files)
        return parsed["name"], files
    raise SkillError("upload a .zip or SKILL.md")


def _safe_https(url: str, hosts: set[str]) -> str:
    clean = public_http_url(url, https_only=True)
    if not clean:
        raise SkillError("invalid url")
    host = (urlparse(clean).hostname or "").lower().rstrip(".")
    if host not in hosts:
        raise SkillError("invalid url")
    if not ips_public(host):
        raise SkillError("invalid url")
    return clean


def _github_cap() -> int:
    return 200 if (settings.github_token or "").strip() else 20


def _take_github() -> None:
    if not limiter.allow("skills-github:global", _github_cap(), 3600):
        raise SkillError("Install rate limit reached. Try again shortly.", 429)


async def _http_get(url: str, *, hosts: set[str], headers: dict | None = None, timeout: float = 8.0):
    import httpx

    clean = _safe_https(url, hosts)
    hdrs = {"User-Agent": "Fuse/skills", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, http2=False) as client:
            return await client.get(clean, headers=hdrs)
    except httpx.HTTPError as exc:
        logger.warning("skills http error: %s", type(exc).__name__)
        raise SkillError("Search is busy. Try again shortly.", 503) from exc


def _map_catalog_hit(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or "")
    skill_id = str(item.get("skillId") or item.get("slug") or item.get("name") or "")
    if not valid_github_source(source) or not skill_id:
        return None
    ident = str(item.get("id") or f"{source}/{skill_id}")
    try:
        installs = int(item.get("installs") or 0)
    except (TypeError, ValueError):
        installs = 0
    return {
        "id": ident,
        "skillId": skill_id,
        "name": str(item.get("name") or skill_id),
        "source": source,
        "installs": installs,
        "url": f"https://skills.sh/{source}/{skill_id}",
    }


async def catalog_search(query: str) -> dict:
    q = (query or "").strip()
    if len(q) < 2 or len(q) > 80:
        raise SkillError("query must be 2–80 characters")
    key = q.lower()
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    token = (settings.skills_sh_token or "").strip()
    if token:
        url = f"https://skills.sh/api/v1/skills/search?q={quote(q)}"
        headers = {"Authorization": f"Bearer {token}"}
    else:
        url = f"https://skills.sh/api/search?q={quote(q)}"
        headers = None
    resp = await _http_get(url, hosts=_SKILLS_HOSTS, headers=headers)
    if resp.status_code == 429:
        raise SkillError("Search is busy. Try again shortly.", 503)
    if resp.status_code in (401, 403):
        raise SkillError("Search is busy. Try again shortly.", 502)
    if resp.status_code >= 500:
        raise SkillError("Search is busy. Try again shortly.", 503)
    if resp.status_code != 200:
        raise SkillError("Search is busy. Try again shortly.", 502)
    try:
        payload = resp.json()
    except Exception as exc:
        raise SkillError("Search is busy. Try again shortly.", 502) from exc
    rows = payload.get("skills") or payload.get("data") or []
    skills = []
    if isinstance(rows, list):
        for item in rows:
            mapped = _map_catalog_hit(item)
            if mapped:
                skills.append(mapped)
    out = {"query": q, "skills": skills}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = (now, out)
    return out


async def _github_get(url: str, *, accept: str = "application/vnd.github+json", timeout: float = 12.0):
    _take_github()
    headers = {"User-Agent": "Fuse/skills", "Accept": accept}
    token = (settings.github_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await _http_get(url, hosts=_GH_HOSTS, headers=headers, timeout=timeout)
    if resp.status_code in (403, 429):
        raise SkillError("Install rate limit reached. Try again shortly.", 429)
    return resp


async def _follow_github(resp) -> bytes:
    import httpx

    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("location") or ""
        _take_github()
        clean = _safe_https(loc, _GH_HOSTS)
        headers = {"User-Agent": "Fuse/skills", "Accept": "application/vnd.github+json"}
        token = (settings.github_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, http2=False) as client:
                resp = await client.get(clean, headers=headers)
        except httpx.HTTPError as exc:
            raise SkillError("Install failed. Try again shortly.", 503) from exc
    if resp.status_code in (403, 429):
        raise SkillError("Install rate limit reached. Try again shortly.", 429)
    if resp.status_code != 200:
        raise SkillError("Could not find SKILL.md")
    data = resp.content
    if len(data) > MAX_ZIPBALL:
        raise SkillError("repository archive too large")
    return data


async def _contents_file(owner: str, repo: str, path: str, branch: str) -> bytes | None:
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/"
        f"{quote(path)}?ref={quote(branch)}"
    )
    resp = await _github_get(url)
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    download = payload.get("download_url") if isinstance(payload, dict) else None
    if not download:
        return None
    _take_github()
    raw = await _http_get(download, hosts=_GH_HOSTS, headers={"User-Agent": "Fuse/skills"}, timeout=12.0)
    if raw.status_code != 200:
        return None
    if len(raw.content) > MAX_FILE:
        raise SkillError("skill file too large")
    return raw.content


async def _fallback_contents(owner: str, repo: str, skill_id: str, branch: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for layout in LAYOUTS:
        path = layout.format(id=skill_id)
        blob = await _contents_file(owner, repo, path, branch)
        if blob is None:
            continue
        files[path] = blob
        # pull a shallow sibling set is skipped — SKILL.md is enough for v1 fallback
        break
    if not files:
        raise SkillError("Could not find SKILL.md (repository tree truncated)")
    return files


async def fetch_github_skill(source: str, skill_id: str) -> dict[str, bytes]:
    if not valid_github_source(source):
        raise SkillError("invalid source")
    if not NAME_RE.fullmatch(skill_id or "") and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", skill_id or ""):
        raise SkillError("invalid skill")
    owner, repo = source.split("/")
    meta = await _github_get(f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}")
    if meta.status_code == 404:
        raise SkillError("repository not found", 404)
    if meta.status_code != 200:
        raise SkillError("Could not find SKILL.md")
    try:
        body = meta.json()
    except Exception as exc:
        raise SkillError("Could not find SKILL.md") from exc
    branch = str(body.get("default_branch") or "main")
    zip_resp = await _github_get(
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/zipball/{quote(branch)}",
        accept="application/vnd.github+json",
        timeout=20.0,
    )
    files: dict[str, bytes] | None = None
    if zip_resp.status_code == 200 or zip_resp.status_code in (301, 302, 303, 307, 308):
        try:
            archive = await _follow_github(zip_resp)
            raw = files_from_zip(archive, max_compressed=MAX_ZIPBALL, loose=True)
            # GitHub zipball prefixes entries with repo-hash/
            stripped: dict[str, bytes] = {}
            for path, blob in raw.items():
                parts = path.split("/", 1)
                stripped[parts[1] if len(parts) == 2 else path] = blob
            files = stripped
        except SkillError as exc:
            if "too large" in exc.detail:
                files = None
            else:
                files = None
    if files is None:
        tree = await _github_get(
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/git/trees/"
            f"{quote(branch)}?recursive=1"
        )
        truncated = False
        if tree.status_code == 200:
            try:
                tbody = tree.json()
                truncated = bool(tbody.get("truncated"))
            except Exception:
                truncated = True
        else:
            truncated = True
        if truncated or files is None:
            files = await _fallback_contents(owner, repo, skill_id, branch)
    slug, sliced = extract_skill_files(files, skill_id=skill_id)
    if slug != skill_id and skill_id.lower() != slug:
        # Allow display-name mismatch only if frontmatter name equals skill_id after parse
        parsed = parse_skill_md(sliced["SKILL.md"].decode("utf-8"))
        if parsed["name"] != skill_id and parsed["name"] != slug:
            raise SkillError("Could not find SKILL.md")
    return sliced
