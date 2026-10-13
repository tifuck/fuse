"""Multiplexed asyncssh pool. Decrypts host secrets with the RAM vault DEK only at connect()."""
from __future__ import annotations

import asyncio
import functools
import hashlib
import ipaddress
import socket
import time
import weakref

from .config import settings
from .hosts import HostError, secret_plain, set_host_status, set_pin
from .search.ssrf import is_blocked_host
from .vault import VaultLocked

_BLOCK_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::ffff:127.0.0.0/104"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

_pool: dict[tuple[int, int], dict] = {}
_lock = asyncio.Lock()
_key_locks: dict[tuple[int, int], asyncio.Lock] = {}
# Bumped by evict(): a connect that started before a pin change or edit must
# not land in the pool afterwards.
_gen: dict[tuple[int, int], int] = {}
IDLE_CLOSE = 300.0


def _connect_gate(key: tuple[int, int]) -> asyncio.Lock:
    """Single-flight per (user, host): two concurrent first uses must not
    both dial and then close the winner's live connection out from under it."""
    gate = _key_locks.get(key)
    if gate is None:
        gate = asyncio.Lock()
        _key_locks[key] = gate
    return gate


class SshError(HostError):
    pass


def _ip_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _BLOCK_NETS):
        return True
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if not settings.allow_private_hosts and (ip.is_private or ip.is_reserved):
        return True
    return False


@functools.lru_cache(maxsize=1)
def _self_addresses() -> frozenset[str]:
    """This server's own addresses: SSH back into Fuse's host stays refused
    even where the address is public (or private and ALLOW_PRIVATE_HOSTS)."""
    names = {socket.gethostname(), socket.getfqdn()}
    base = (settings.public_base_url or "").split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
    if base:
        names.add(base)
    out: set[str] = set()
    for name in names:
        try:
            out.update(info[4][0] for info in socket.getaddrinfo(name, None))
        except OSError:
            continue
    return frozenset(out)


def resolve_public(hostname: str, port: int) -> list[str]:
    host = (hostname or "").lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    # Literal IPs are judged by _ip_forbidden alone (it honours
    # ALLOW_PRIVATE_HOSTS); the name denylist would refuse every RFC1918 one.
    if not host or (literal is None and is_blocked_host(host)):
        raise SshError("host is not allowed")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SshError("hostname could not be resolved") from exc
    self_addrs = _self_addresses()
    addrs = []
    for info in infos:
        ip_s = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            continue
        if _ip_forbidden(ip):
            raise SshError("host resolves to a blocked address")
        if ip_s in self_addrs:
            raise SshError("host is this Fuse server")
        if ip_s not in addrs:
            addrs.append(ip_s)
    if not addrs:
        raise SshError("host resolves to a blocked address")
    return addrs


def _peer_ok(peer: str) -> None:
    try:
        ip = ipaddress.ip_address(peer.split("%")[0])
    except ValueError as exc:
        raise SshError("invalid peer address") from exc
    if _ip_forbidden(ip):
        raise SshError("connected peer is blocked")


def _key_id(key) -> tuple[str, str]:
    algo = getattr(key, "algorithm", None) or "ssh-ed25519"
    if callable(algo):
        algo = algo()
    exported = key.export_public_key()
    if isinstance(exported, str):
        exported = exported.encode()
    digest = hashlib.sha256(exported).hexdigest()
    return str(algo), digest


async def _connect_once(user_id: int, host_id: int, *, probe: bool):
    import asyncssh

    secret, passphrase, pub = secret_plain(user_id, host_id)
    if pub.get("auth_type") == "agent":
        raise SshError("connector hosts are not SSH")
    addrs = resolve_public(pub["hostname"], pub["port"])
    kwargs = {
        "host": addrs[0],
        "port": pub["port"],
        "username": pub["username"],
        "known_hosts": None,
        "agent_forwarding": False,
        "keepalive_interval": 30,
        "connect_timeout": 15,
    }
    if probe:
        # Harvest the host key without authenticating: no password or key is
        # ever sent to a host that has not been pinned yet.
        captured: dict = {}

        class _ProbeClient(asyncssh.SSHClient):
            def validate_host_public_key(self, host, addr, port, key):
                captured["algo"], captured["digest"] = _key_id(key)
                return True

        kwargs["known_hosts"] = b""
        kwargs["client_factory"] = _ProbeClient
        try:
            conn = await asyncssh.connect(**kwargs)
        except Exception:
            # Auth is refused once the key was captured; that is the probe.
            if "digest" in captured:
                return None, captured["algo"], captured["digest"]
            raise
        try:
            algo, digest = _key_id(conn.get_server_host_key())
        finally:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass
        return None, algo, digest
    if not pub["fingerprint"]:
        raise SshError("host is not trusted yet")
    expected = pub["fingerprint"]
    expected_algo = pub.get("key_algo") or ""

    class _PinClient(asyncssh.SSHClient):
        def validate_host_public_key(self, host, addr, port, key):
            algo, digest = _key_id(key)
            if expected_algo and algo != expected_algo:
                return False
            return digest == expected

    kwargs["known_hosts"] = b""
    kwargs["client_factory"] = _PinClient
    if pub["auth_type"] == "password":
        kwargs["password"] = secret
        kwargs["client_keys"] = None
    else:
        kwargs["client_keys"] = [asyncssh.import_private_key(secret, passphrase=passphrase or None)]
        kwargs["password"] = None
    conn = await asyncssh.connect(**kwargs)
    try:
        peer = conn.get_extra_info("peername")
        if peer:
            _peer_ok(str(peer[0]))
        key = conn.get_server_host_key()
        algo, digest = _key_id(key)
        if digest != pub["fingerprint"] or (pub["key_algo"] and algo != pub["key_algo"]):
            conn.close()
            raise SshError("host key changed")
        result = await conn.run("echo ok", check=False, timeout=10)
        out = (result.stdout or "").strip()
        if result.exit_status not in (0, None) or "ok" not in out.lower():
            raise SshError("remote echo failed")
        return conn, algo, digest
    except BaseException:
        # Cancellation included: an authenticated connection nobody pooled
        # would otherwise stay open on its keepalives.
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:
            pass
        raise


async def probe(user_id: int, host_id: int) -> dict:
    try:
        _conn, algo, digest = await _connect_once(user_id, host_id, probe=True)
    except VaultLocked as exc:
        raise SshError("vault locked", 401) from exc
    except HostError as exc:
        if exc.status_code == 401:  # secret_plain's vault-locked HostError
            raise SshError("vault locked", 401) from exc
        set_host_status(user_id, host_id, ok=False, error=exc.detail)
        raise SshError(exc.detail, exc.status_code) from exc
    except Exception as exc:
        set_host_status(user_id, host_id, ok=False, error="probe failed")
        raise SshError("probe failed") from exc
    return {"algorithm": algo, "fingerprint": digest}


async def trust(user_id: int, host_id: int, algorithm: str, fingerprint: str) -> dict:
    item = set_pin(user_id, host_id, algorithm, fingerprint)
    # A connection authenticated against the previous key must not keep
    # serving commands after the user trusted a different one.
    evict(user_id, host_id)
    return item


async def test(user_id: int, host_id: int) -> dict:
    try:
        conn, algo, digest = await _connect_once(user_id, host_id, probe=False)
    except VaultLocked as exc:
        raise SshError("vault locked", 401) from exc
    except HostError as exc:
        if exc.status_code == 401:  # secret_plain's vault-locked HostError
            raise SshError("vault locked", 401) from exc
        set_host_status(user_id, host_id, ok=False, error=exc.detail)
        raise SshError(exc.detail, exc.status_code) from exc
    except Exception as exc:
        set_host_status(user_id, host_id, ok=False, error="test failed")
        raise SshError("test failed") from exc
    conn.close()
    try:
        await conn.wait_closed()
    except Exception:
        pass
    set_host_status(user_id, host_id, ok=True)
    return {"ok": True, "algorithm": algo, "fingerprint": digest}


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def evict(user_id: int, host_id: int) -> None:
    """Drop the pooled connection for a host that was edited or deleted.

    Callable from sync code: pops are atomic dict ops and asyncssh close()
    only shuts the transport, no await needed."""
    key = (int(user_id), int(host_id))
    _gen[key] = _gen.get(key, 0) + 1
    rec = _pool.pop(key, None)
    _key_locks.pop(key, None)
    if rec is not None and rec.get("conn") is not None:
        _close(rec["conn"])


def _reusable(rec) -> bool:
    if not (rec and rec.get("conn") and not rec["conn"].is_closed()):
        return False
    # Live channels (a background command, a monitor) mean the connection is
    # in use however long ago it was checked out: reuse it, never close it
    # under the job.
    return time.monotonic() - float(rec.get("last") or 0) <= IDLE_CLOSE or _busy(rec["conn"])


async def close_user(user_id: int) -> None:
    async with _lock:
        keys = [k for k in _pool if k[0] == int(user_id)]
        items = [_pool.pop(k) for k in keys]
    for item in items:
        conn = item.get("conn")
        if conn is None:
            continue
        try:
            conn.close()
        except Exception:
            pass
        try:
            await conn.wait_closed()
        except Exception:
            pass


async def connection(user_id: int, host_id: int):
    """Pinned connect; reuse idle multiplexed sessions."""
    key = (int(user_id), int(host_id))
    async with _lock:
        rec = _pool.get(key)
        if _reusable(rec):
            rec["last"] = time.monotonic()
            return rec["conn"]
        stale = _pool.pop(key, None)
    if stale and stale.get("conn") is not None:
        _close(stale["conn"])
    async with _connect_gate(key):
        # Another caller may have connected while we waited for the gate.
        async with _lock:
            rec = _pool.get(key)
            if _reusable(rec):
                rec["last"] = time.monotonic()
                return rec["conn"]
        gen = _gen.get(key, 0)
        conn, _algo, _digest = await _connect_once(user_id, host_id, probe=False)
        async with _lock:
            current = _gen.get(key, 0) == gen
            if current:
                _pool[key] = {"conn": conn, "last": time.monotonic()}
        if not current:
            # Evicted mid-connect (pin or host changed): never hand this out.
            _close(conn)
            return await connection(user_id, host_id)
        return conn


# Channels a cache holds open on purpose (workspace's pooled SFTP client).
# They do not make a connection busy, or it could never be reaped.
_cached_channels: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def note_cached_channel(conn, delta: int = 1) -> None:
    try:
        _cached_channels[conn] = max(0, _cached_channels.get(conn, 0) + delta)
    except TypeError:
        pass  # not weak-referenceable


def _busy(conn) -> bool:
    # Open channels mean a command may still be running on it. Unknown
    # (attribute missing) counts as busy: never close what may be in use.
    channels = getattr(conn, "_channels", None)
    if channels is None:
        return True
    try:
        cached = _cached_channels.get(conn, 0)
    except TypeError:
        cached = 0
    return len(channels) > cached


async def reap_idle() -> None:
    """Close pooled connections idle past IDLE_CLOSE. Reuse only checked
    expiry on access, so unused sessions (and their keepalives) stayed open
    until logout or shutdown."""
    async with _lock:
        stale = [k for k, rec in _pool.items()
                 if not _reusable(rec) and not (rec.get("conn") is not None and _busy(rec["conn"]))]
        items = [_pool.pop(k) for k in stale]
    for item in items:
        if item.get("conn") is not None:
            _close(item["conn"])


async def drain() -> None:
    async with _lock:
        items = list(_pool.values())
        _pool.clear()
    for item in items:
        conn = item.get("conn")
        if conn is None:
            continue
        try:
            conn.close()
        except Exception:
            pass
        try:
            await conn.wait_closed()
        except Exception:
            pass
