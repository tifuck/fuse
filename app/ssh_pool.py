"""Multiplexed asyncssh pool. Decrypts host secrets with the RAM vault DEK only at connect()."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import time

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


def resolve_public(hostname: str, port: int) -> list[str]:
    host = (hostname or "").lower().rstrip(".")
    if not host or is_blocked_host(host):
        raise SshError("host is not allowed")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SshError("hostname could not be resolved") from exc
    addrs = []
    for info in infos:
        ip_s = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            continue
        if _ip_forbidden(ip):
            raise SshError("host resolves to a blocked address")
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
    if not probe:
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
        if not probe:
            if digest != pub["fingerprint"] or (pub["key_algo"] and algo != pub["key_algo"]):
                conn.close()
                raise SshError("host key changed")
        result = await conn.run("echo ok", check=False, timeout=10)
        out = (result.stdout or "").strip()
        if result.exit_status not in (0, None) or "ok" not in out.lower():
            raise SshError("remote echo failed")
        return conn, algo, digest
    except Exception:
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:
            pass
        raise
    finally:
        secret = ""
        passphrase = ""


async def probe(user_id: int, host_id: int) -> dict:
    try:
        conn, algo, digest = await _connect_once(user_id, host_id, probe=True)
    except VaultLocked as exc:
        raise SshError("vault locked", 401) from exc
    except SshError:
        raise
    except Exception as exc:
        set_host_status(user_id, host_id, ok=False, error="probe failed")
        raise SshError("probe failed") from exc
    conn.close()
    try:
        await conn.wait_closed()
    except Exception:
        pass
    return {"algorithm": algo, "fingerprint": digest}


async def trust(user_id: int, host_id: int, algorithm: str, fingerprint: str) -> dict:
    return set_pin(user_id, host_id, algorithm, fingerprint)


async def test(user_id: int, host_id: int) -> dict:
    try:
        conn, algo, digest = await _connect_once(user_id, host_id, probe=False)
    except VaultLocked as exc:
        raise SshError("vault locked", 401) from exc
    except SshError as exc:
        set_host_status(user_id, host_id, ok=False, error=exc.detail)
        raise
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


async def connection(user_id: int, host_id: int):
    """Pinned connect; reuse idle multiplexed sessions."""
    key = (int(user_id), int(host_id))
    async with _lock:
        rec = _pool.get(key)
        if rec and rec.get("conn") and not rec["conn"].is_closed():
            rec["last"] = time.monotonic()
            return rec["conn"]
    async with _connect_gate(key):
        # Another caller may have connected while we waited for the gate.
        async with _lock:
            rec = _pool.get(key)
            if rec and rec.get("conn") and not rec["conn"].is_closed():
                rec["last"] = time.monotonic()
                return rec["conn"]
        conn, _algo, _digest = await _connect_once(user_id, host_id, probe=False)
        async with _lock:
            _pool[key] = {"conn": conn, "last": time.monotonic()}
        return conn


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
