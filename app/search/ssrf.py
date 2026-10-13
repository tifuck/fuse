from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import parse_qs, quote, urlparse, urlunparse

_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "ip6-localhost",
    "ip6-loopback",
}


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def is_blocked_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS:
        return True
    if host.endswith(".internal") or host.endswith(".local") or host.endswith(".localhost"):
        return True
    if host.startswith("169.254."):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _netloc(host: str, port: int | None, scheme: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    if port in (None, default):
        return host
    return f"{host}:{port}"


def public_http_url(raw: str, *, https_only: bool = False) -> str:
    url = str(raw or "").strip()
    if not url or len(url) > 2000:
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except Exception:
        return ""
    if parsed.scheme not in (("https",) if https_only else ("http", "https")):
        return ""
    if parsed.username or parsed.password:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if is_blocked_host(host):
        return ""
    if port not in (None, 80, 443):
        return ""
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    query = (parsed.query or "")[:800]
    netloc = _netloc(host, port, parsed.scheme)
    return urlunparse((parsed.scheme, netloc, path, "", query, ""))


def decode_ddg_url(href: str) -> str:
    href = html.unescape(str(href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parsed.path.startswith("/l"):
        qs = parse_qs(parsed.query)
        target = (qs.get("uddg") or [""])[0]
        if target:
            href = target  # parse_qs already decoded it; a second pass alters %2F etc.
    return public_http_url(href)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = (parsed.scheme or "https").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    query = parsed.query or ""
    return f"{scheme}://{_netloc(host, port, scheme)}{path}" + (f"?{query}" if query else "")


def allowed_fetch_url(url: str, allowed: set[str] | None = None) -> str:
    """Public HTTPS whose host passes the site domain policy. `allowed` is unused."""
    clean = public_http_url(url, https_only=True)
    if not clean:
        return ""
    host = host_of(clean)
    if not host or not domain_allowed(host):
        return ""
    return clean


_DOMAIN_RE = re.compile(r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def domain_pattern(raw: str) -> str:
    """Normalize one blacklist/whitelist entry. '' if it is not a hostname or *.suffix."""
    text = (raw or "").strip().lower().rstrip(".")
    if text.startswith("https://") or text.startswith("http://"):
        text = host_of(text)
    if "/" in text or "@" in text or ":" in text or " " in text:
        return ""
    if text.startswith("*."):
        rest = text[2:]
        if not rest or not _DOMAIN_RE.match(rest):
            return ""
        return "*." + rest
    if not text or not _DOMAIN_RE.match(text):
        return ""
    return text


def parse_domain_list(raw: str, *, limit: int = 500) -> list[str]:
    out: list[str] = []
    for line in str(raw or "").replace(",", "\n").splitlines():
        item = domain_pattern(line.split("#", 1)[0])
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def host_matches(host: str, pattern: str) -> bool:
    host = (host or "").lower().rstrip(".")
    pattern = (pattern or "").lower()
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == pattern


def domain_allowed(host: str, *, blacklist: list[str] | None = None, whitelist: list[str] | None = None) -> bool:
    """Blacklist always blocks. A non-empty whitelist blocks everything else."""
    if blacklist is None or whitelist is None:
        from .policy import domain_lists

        stored_black, stored_white = domain_lists()
        if blacklist is None:
            blacklist = stored_black
        if whitelist is None:
            whitelist = stored_white
    if any(host_matches(host, item) for item in blacklist):
        return False
    if whitelist and not any(host_matches(host, item) for item in whitelist):
        return False
    return True


def public_ips(host: str, port: int = 443) -> list[str]:
    host = (host or "").lower().rstrip(".")
    if not host or is_blocked_host(host):
        return []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):  # UnicodeError: a label longer than 63 chars
        return []
    addrs: list[str] = []
    for info in infos:
        ip_s = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            return []
        if not ip.is_global:
            return []
        if ip_s not in addrs:
            addrs.append(ip_s)
    return addrs


def ips_public(host: str) -> bool:
    return bool(public_ips(host))


def pin_https_url(url: str, ip: str) -> str:
    parsed = urlparse(url)
    port = parsed.port or 443
    if ":" in ip:
        netloc = f"[{ip}]:{port}"
    else:
        netloc = f"{ip}:{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, ""))


def wiki_page_url(title: str) -> str:
    slug = quote((title or "").replace(" ", "_"), safe="()_,-'")
    return public_http_url(f"https://en.wikipedia.org/wiki/{slug}") or ""
