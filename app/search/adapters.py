"""Site adapters for read_url: a cleaner or reachable source for a page.

Each adapter maps a URL the model asked for onto public APIs or raw files
that carry the same content without the parts that break reading:

- GitHub file views (``/blob/``) are a React shell around embedded JSON; the
  raw file is the content itself.
- Stack Exchange question pages refuse automated readers (HTTP 403) from
  most server IPs; the public API serves the question and its answers.
- npm package pages refuse automated readers; the registry manifest and the
  package README (via the jsDelivr package CDN) carry what the page shows.

Adapters only fetch through the caller's SSRF-checked downloader. A failed
adapter is not an error: the caller falls back to fetching the page itself.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlparse

# (final_url, content_type, body_text)
Downloaded = tuple[str, str, str]
Downloader = Callable[[str, str], Awaitable[Downloaded]]

_GITHUB_BLOB = re.compile(r"^/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$")
_SE_QUESTION = re.compile(r"^/questions/(\d+)(?:/|$)")
_SE_SITES = {
    "stackoverflow.com": "stackoverflow",
    "superuser.com": "superuser",
    "serverfault.com": "serverfault",
    "askubuntu.com": "askubuntu",
    "mathoverflow.net": "mathoverflow.net",
    "stackapps.com": "stackapps",
}
_NPM_PACKAGE = re.compile(r"^/package/((?:@[^/]+/)?[^/]+)/?(?:v/([^/]+))?")
SE_ANSWERS = 6
JSON_ACCEPT = "application/json"
TEXT_ACCEPT = "text/plain, text/markdown, */*;q=0.5"


@dataclass(frozen=True)
class Plan:
    kind: str
    url: str
    target: str = ""
    site: str = ""
    ident: str = ""
    version: str = ""


def plan_for(url: str) -> Plan | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if host in ("github.com", "www.github.com"):
        match = _GITHUB_BLOB.match(path)
        if match:
            owner, repo, rest = match.groups()
            raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
            return Plan("raw", url, target=raw)
        return None
    site = _SE_SITES.get(host.removeprefix("www."))
    if site is None and host.endswith(".stackexchange.com") and host.count(".") == 2:
        site = host.split(".", 1)[0]
    if site:
        match = _SE_QUESTION.match(path)
        if match:
            return Plan("stackexchange", url, site=site, ident=match.group(1))
        return None
    if host in ("www.npmjs.com", "npmjs.com"):
        match = _NPM_PACKAGE.match(path)
        if match:
            return Plan("npm", url, ident=match.group(1), version=match.group(2) or "latest")
    return None


async def run(plan: Plan, download: Downloader) -> Downloaded | None:
    if plan.kind == "raw":
        _, ctype, body = await download(plan.target, TEXT_ACCEPT)
        return plan.url, ctype, body
    if plan.kind == "stackexchange":
        return await _stackexchange(plan, download)
    if plan.kind == "npm":
        return await _npm(plan, download)
    return None


def _json(body: str):
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None


async def _stackexchange(plan: Plan, download: Downloader) -> Downloaded | None:
    base = "https://api.stackexchange.com/2.3/questions/" + plan.ident
    common = f"site={quote(plan.site)}&filter=withbody"
    _, _, q_body = await download(f"{base}?{common}", JSON_ACCEPT)
    question = (_json(q_body) or {}).get("items") or []
    if not question:
        return None
    q = question[0]
    _, _, a_body = await download(
        f"{base}/answers?{common}&sort=votes&order=desc&pagesize={SE_ANSWERS}", JSON_ACCEPT
    )
    answers = (_json(a_body) or {}).get("items") or []
    accepted = q.get("accepted_answer_id")
    answers.sort(key=lambda a: (a.get("answer_id") != accepted, -int(a.get("score") or 0)))
    title = html_lib.escape(html_lib.unescape(str(q.get("title") or "")))
    tags = ", ".join(str(t) for t in q.get("tags") or [])
    parts = [
        f"<html><head><title>{title}</title></head><body><article>",
        f"<h1>{title}</h1>",
        f"<p>Question score {int(q.get('score') or 0)} · {int(q.get('answer_count') or 0)} answers"
        + (f" · tags: {html_lib.escape(tags)}" if tags else "") + "</p>",
        str(q.get("body") or ""),
    ]
    for answer in answers:
        label = "Accepted answer" if answer.get("answer_id") == accepted else "Answer"
        parts.append(f"<h2>{label} (score {int(answer.get('score') or 0)})</h2>")
        parts.append(str(answer.get("body") or ""))
    if int(q.get("answer_count") or 0) > len(answers):
        parts.append(f"<p>({int(q['answer_count']) - len(answers)} lower-voted answers not shown.)</p>")
    parts.append("</article></body></html>")
    return plan.url, "text/html; source=stackexchange-api", "".join(parts)


async def _npm(plan: Plan, download: Downloader) -> Downloaded | None:
    name = plan.ident
    _, _, body = await download(f"https://registry.npmjs.org/{name}/{quote(plan.version, safe='')}", JSON_ACCEPT)
    manifest = _json(body)
    if not isinstance(manifest, dict) or not manifest.get("name"):
        return None
    version = str(manifest.get("version") or plan.version)
    lines = [f"# {manifest['name']} {version}"]
    if manifest.get("description"):
        lines.append(str(manifest["description"]))
    facts = []
    repo = manifest.get("repository")
    if isinstance(repo, dict):
        repo = repo.get("url")
    for label, value in (
        ("License", manifest.get("license")),
        ("Homepage", manifest.get("homepage")),
        ("Repository", repo),
        ("Install", f"npm install {manifest['name']}"),
    ):
        if value:
            facts.append(f"- {label}: {value}")
    deps = manifest.get("dependencies") or {}
    if isinstance(deps, dict) and deps:
        facts.append("- Dependencies: " + ", ".join(f"{k} {v}" for k, v in list(deps.items())[:40]))
    if facts:
        lines.append("\n".join(facts))
    readme = await _npm_readme(name, version, download)
    if readme:
        lines.append(readme)
    return plan.url, "text/markdown; source=npm-registry", "\n\n".join(lines)


async def _npm_readme(name: str, version: str, download: Downloader) -> str:
    try:
        _, _, listing = await download(f"https://data.jsdelivr.com/v1/packages/npm/{name}@{quote(version)}", JSON_ACCEPT)
    except Exception:
        return ""
    files = (_json(listing) or {}).get("files") or []
    readme = next(
        (f.get("name") for f in files if isinstance(f, dict) and f.get("type") == "file"
         and str(f.get("name") or "").lower().startswith("readme")),
        None,
    )
    if not readme:
        return ""
    try:
        _, _, text = await download(f"https://cdn.jsdelivr.net/npm/{name}@{quote(version)}/{quote(readme)}", TEXT_ACCEPT)
    except Exception:
        return ""
    return text
