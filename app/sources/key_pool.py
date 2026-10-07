import asyncio
import email.utils
import hashlib
import json
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default cooldown tiers (seconds). Overridable via KeyPool constructor.
# ---------------------------------------------------------------------------

DEFAULTS = {
    "rest_seconds":           120.0,   # post-success rest window so the next request prefers another key
    "transient_429":            60.0,   # 429 with no quota keyword, no Retry-After
    "hourly_quota":            1200.0,  # 429 body contains hourly quota language
    "session_quota":          18000.0,  # Ollama Cloud session usage limit (~5h)
    "weekly_quota":          172800.0,  # exhausted / weekly usage — 48h cooldown
    "account_unavailable":    86400.0,  # "account is currently unavailable" flag — 24h, self-heals
    "weekly_disable_threshold": 3600.0, # Retry-After >= this => DISABLED (weekly)
    "5xx_base":                  10.0,
    "5xx_cap":                  120.0,
    "net_base":                   5.0,
    "net_cap":                   60.0,
    "other_4xx":                 30.0,
    "auth_disable_threshold":     3,    # consecutive 401/403 before DISABLED
    "decay_factor":             10.0,  # idle window = N * last_backoff
    "sweeper_interval":         30.0,
    "max_retries":                5,
}


class NoAvailableKeyError(ConnectionError):
    """Raised when every key is DISABLED and no key can be selected."""


# ---------------------------------------------------------------------------
# KeyState — a small state machine per Ollama Cloud API key
# ---------------------------------------------------------------------------

# States
ACTIVE   = "active"    # ready, no rest/cooldown
RESTING  = "resting"   # used successfully < rest_seconds ago; fallback (LRU)
COOLING  = "cooling"   # error cooldown in effect; last-resort candidate
DISABLED = "disabled"  # auth failures or weekly quota; excluded from rotation

DISABLED_AUTH    = "auth_failure"
DISABLED_WEEKLY  = "weekly_quota"
DISABLED_SESSION = "session_quota"
DISABLED_UNAVAILABLE = "account_unavailable"
_QUOTA_DISABLES = {DISABLED_WEEKLY, DISABLED_SESSION, DISABLED_UNAVAILABLE}


class KeyState:
    """Per-key state machine tracking usage, rest, cooldowns, and disables."""

    __slots__ = (
        "key",
        "state",
        "available_at",        # epoch seconds when RESTING/COOLING/DISABLED expires; 0 if ACTIVE
        "disabled_reason",     # "auth_failure" | "weekly_quota" | None
        "total_requests",
        "total_errors",
        "last_used",
        "last_error_type",
        "last_error_at",
        "last_status",
        "last_error_body",
        "last_model",
        "consecutive_errors",
        "consecutive_auth_errors",
        "last_backoff",        # last applied backoff seconds (for decay math)
    )

    def __init__(self, key: str):
        self.key = key
        self.state = ACTIVE
        self.available_at = 0.0
        self.disabled_reason: Optional[str] = None
        self.total_requests = 0
        self.total_errors = 0
        self.last_used = 0.0
        self.last_error_type: Optional[str] = None
        self.last_error_at = 0.0
        self.last_status = 0
        self.last_error_body = ""
        self.last_model = ""
        self.consecutive_errors = 0
        self.consecutive_auth_errors = 0
        self.last_backoff = 0.0

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "available_at": self.available_at,
            "disabled_reason": self.disabled_reason,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "last_used": self.last_used,
            "last_error_type": self.last_error_type,
            "last_error_at": self.last_error_at,
            "last_status": self.last_status,
            "last_error_body": self.last_error_body,
            "last_model": self.last_model,
            "consecutive_errors": self.consecutive_errors,
            "consecutive_auth_errors": self.consecutive_auth_errors,
            "last_backoff": self.last_backoff,
        }

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "KeyState":
        s = cls(key)
        s.state = data.get("state", ACTIVE)
        s.available_at = data.get("available_at", 0.0)
        s.disabled_reason = data.get("disabled_reason")
        s.total_requests = data.get("total_requests", 0)
        s.total_errors = data.get("total_errors", 0)
        s.last_used = data.get("last_used", 0.0)
        s.last_error_type = data.get("last_error_type")
        s.last_error_at = data.get("last_error_at", 0.0)
        s.last_status = int(data.get("last_status") or 0)
        s.last_error_body = str(data.get("last_error_body") or "")[:200]
        s.last_model = str(data.get("last_model") or "")[:80]
        s.consecutive_errors = data.get("consecutive_errors", 0)
        s.consecutive_auth_errors = data.get("consecutive_auth_errors", 0)
        s.last_backoff = data.get("last_backoff", 0.0)

        # Migration on load: expire stale cooldowns/rests.
        now = time.time()
        if s.state in (RESTING, COOLING) and s.available_at <= now:
            s.state = ACTIVE
            s.available_at = 0.0
        # DISABLED auth stays disabled across restart (operator must edit state file).
        # Quota disables (weekly / session) re-enable when available_at expires.
        if s.state == DISABLED and s.disabled_reason in _QUOTA_DISABLES and s.available_at <= now:
            s.state = ACTIVE
            s.available_at = 0.0
            s.disabled_reason = None
            s.consecutive_errors = 0
            s.last_backoff = 0.0
        return s

    # -- state transitions -------------------------------------------------

    def is_working(self) -> bool:
        """A 'working' key is selectable (not DISABLED). ACTIVE/RESTING/COOLING."""
        return self.state != DISABLED

    def refresh_state(self) -> None:
        """Lazily transition RESTING/COOLING/DISABLED -> ACTIVE when expired."""
        now = time.time()
        if self.state in (RESTING, COOLING) and self.available_at <= now:
            self.state = ACTIVE
            self.available_at = 0.0
        elif (
            self.state == DISABLED
            and self.disabled_reason in _QUOTA_DISABLES
            and self.available_at <= now
        ):
            self.state = ACTIVE
            self.available_at = 0.0
            self.disabled_reason = None
            self.consecutive_errors = 0
            self.last_backoff = 0.0

    def remember(self, *, status: int = 0, body: str = "", model: str = "", error_type: str | None = None) -> None:
        if status:
            self.last_status = int(status)
        if body:
            self.last_error_body = body[:200]
        if model:
            self.last_model = model[:80]
        if error_type:
            self.last_error_type = error_type
            self.last_error_at = time.time()

    def mark_success(self, rest_seconds: float) -> None:
        now = time.time()
        self.total_requests += 1
        self.last_used = now
        self.consecutive_errors = 0
        self.consecutive_auth_errors = 0
        self.last_backoff = 0.0
        self.last_status = 200
        self.last_error_body = ""
        # Rest window: don't pick this key again for `rest_seconds` unless
        # it's the LRU fallback.
        self.state = RESTING
        self.available_at = now + rest_seconds

    def mark_error(
        self,
        error_type: str,
        cooldown: float,
        disable: bool = False,
        disable_reason: Optional[str] = None,
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_errors += 1
        self.last_error_type = error_type
        self.last_error_at = now
        self.consecutive_errors += 1
        self.last_backoff = cooldown

        if disable:
            self.state = DISABLED
            self.disabled_reason = disable_reason
            # available_at=0 for auth (permanent); set externally for weekly
            if disable_reason == DISABLED_AUTH:
                self.available_at = 0.0
        elif cooldown > 0:
            self.state = COOLING
            self.available_at = now + cooldown
        else:
            # No cooldown (e.g. 400 bad_request) — stay ACTIVE
            self.state = ACTIVE
            self.available_at = 0.0

    def decay_errors(self, decay_factor: float) -> bool:
        """Decay error counters if idle long enough. Returns True if changed."""
        now = time.time()
        changed = False

        if self.consecutive_errors > 0 and self.last_backoff > 0:
            idle = now - self.last_error_at
            if idle > decay_factor * self.last_backoff:
                self.consecutive_errors = max(0, self.consecutive_errors // 2)
                self.last_backoff = max(1.0, self.last_backoff // 2)
                changed = True

        # 401/403 below the disable threshold leave the key ACTIVE with no
        # backoff window, so consecutive_auth_errors is not covered above and
        # would otherwise never decay — a brief auth burst that recovers would
        # leave the key permanently one failure away from being disabled. Decay
        # one auth error per idle window (decay_factor minutes by default).
        if self.consecutive_auth_errors > 0:
            idle = now - self.last_error_at
            if idle > decay_factor * 60.0:
                self.consecutive_auth_errors -= 1
                changed = True

        return changed

    # -- introspection -----------------------------------------------------

    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    def available_in(self) -> float:
        """Seconds until this key becomes ACTIVE (0 if already active)."""
        if self.state == ACTIVE:
            return 0.0
        return max(0.0, self.available_at - time.time())

    def rest_remaining(self) -> float:
        """Seconds remaining in RESTING window (0 if not resting)."""
        if self.state != RESTING:
            return 0.0
        return max(0.0, self.available_at - time.time())

    def masked(self) -> str:
        # Non-secret identifier for logs: a hash prefix, not key material.
        return "sha256:" + hashlib.sha256(self.key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------

def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header into seconds. Returns None if unparseable.

    Supports delta-seconds and HTTP-date per RFC 7231.
    """
    if not header_value:
        return None
    v = header_value.strip()
    # Try integer seconds first
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    # Try HTTP-date
    try:
        dt = email.utils.parsedate_to_datetime(v)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 429 body classification
# ---------------------------------------------------------------------------

def classify_429(body_text: str) -> str:
    """Classify a 429 body as 'weekly' | 'session' | 'hourly' | 'transient'.

    Ollama Cloud emits two quota 429s we care about:
    - "weekly usage limit" — account is spent; keep it off the pool for ~48h
    - "session usage limit" — rolling ~5h window; skip until that window ends

    Prefer specific phrases over the bare words 'week'/'hour', which show up in
    unrelated prose. Weekly wins over session/hourly if both appear.
    """
    if not body_text:
        return "transient"
    lower = body_text[:500].lower()
    weekly_markers = (
        "weekly limit",
        "weekly quota",
        "weekly usage",
        "per week",
        "per-week",
        "week limit",
        "reached your weekly",
        "out of usage",
        "usage has been exhausted",
    )
    session_markers = (
        "session usage",
        "session limit",
        "session quota",
        "5 hour",
        "5-hour",
        "five hour",
    )
    hourly_markers = ("hourly limit", "hourly quota", "per hour", "per-hour", "hour limit")
    if any(m in lower for m in weekly_markers):
        return "weekly"
    if any(m in lower for m in session_markers):
        return "session"
    if any(m in lower for m in hourly_markers):
        return "hourly"
    if "usage limit" in lower or "quota" in lower:
        return "weekly"
    return "transient"


# ---------------------------------------------------------------------------
# KeyPool — smart rotation with rest windows and cooldown tiers
# ---------------------------------------------------------------------------

class KeyPool:
    """Manages a pool of Ollama Cloud API keys with smart rotation."""

    def __init__(
        self,
        keys_file: str = "ollama-keys.txt",
        state_file: str = "key_state.json",
        max_retries: int = DEFAULTS["max_retries"],
        rest_seconds: float = DEFAULTS["rest_seconds"],
        transient_429: float = DEFAULTS["transient_429"],
        hourly_quota: float = DEFAULTS["hourly_quota"],
        session_quota: float = DEFAULTS["session_quota"],
        weekly_quota: float = DEFAULTS["weekly_quota"],
        account_unavailable: float = DEFAULTS["account_unavailable"],
        weekly_disable_threshold: float = DEFAULTS["weekly_disable_threshold"],
        five_xx_base: float = DEFAULTS["5xx_base"],
        five_xx_cap: float = DEFAULTS["5xx_cap"],
        net_base: float = DEFAULTS["net_base"],
        net_cap: float = DEFAULTS["net_cap"],
        other_4xx: float = DEFAULTS["other_4xx"],
        auth_disable_threshold: int = DEFAULTS["auth_disable_threshold"],
        decay_factor: float = DEFAULTS["decay_factor"],
        sweeper_interval: float = DEFAULTS["sweeper_interval"],
    ):
        self.keys_file = Path(keys_file)
        self.state_file = Path(state_file)
        self.max_retries = max_retries

        self.rest_seconds = rest_seconds
        self.transient_429 = transient_429
        self.hourly_quota = hourly_quota
        self.session_quota = session_quota
        self.weekly_quota = weekly_quota
        self.account_unavailable = account_unavailable
        self.weekly_disable_threshold = weekly_disable_threshold
        self.five_xx_base = five_xx_base
        self.five_xx_cap = five_xx_cap
        self.net_base = net_base
        self.net_cap = net_cap
        self.other_4xx = other_4xx
        self.auth_disable_threshold = auth_disable_threshold
        self.decay_factor = decay_factor
        self.sweeper_interval = sweeper_interval

        self.states: dict[str, KeyState] = {}
        self._key_order: list[str] = []
        self._sweeper_task: Optional[asyncio.Task] = None
        self._save_state_dirty = False
        self._save_lock = threading.Lock()

    # -- loading & persistence --------------------------------------------

    def load_keys(self) -> int:
        if not self.keys_file.exists():
            raise FileNotFoundError(f"Keys file not found: {self.keys_file}")

        # Warn if the secret keys file is group/world readable.
        try:
            mode = self.keys_file.stat().st_mode
            if mode & 0o077:
                logger.warning(
                    "Keys file %s is group/world accessible (mode %o); "
                    "restrict it with chmod 600 to protect key material",
                    self.keys_file, mode & 0o777,
                )
        except OSError as e:
            logger.warning("Could not stat keys file %s: %s", self.keys_file, e)

        with open(self.keys_file, "r") as f:
            raw_lines = f.read().splitlines()

        keys: list[str] = []
        for line in raw_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for part in line.split(","):
                part = part.strip()
                if part and not part.startswith("#"):
                    keys.append(part)

        # Deduplicate, preserve order
        seen: set[str] = set()
        unique: list[str] = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique.append(k)

        self._key_order = unique
        for k in unique:
            self.states[k] = KeyState(k)

        # Restore persisted state on top of fresh KeyState objects
        self.load_state()

        logger.info("Loaded %d keys from %s", len(unique), self.keys_file)
        return len(unique)

    def load_from_list(self, keys: list[str]) -> int:
        seen: set[str] = set()
        unique: list[str] = []
        for k in keys:
            k = (k or "").strip()
            if k and k not in seen:
                seen.add(k)
                unique.append(k)
        self._key_order = unique
        for k in unique:
            if k not in self.states:
                self.states[k] = KeyState(k)
        for k in list(self.states):
            if k not in seen:
                self.states.pop(k, None)
        self.load_state()
        logger.info("Loaded %d keys from list", len(unique))
        return len(unique)

    def load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            by_hash = {hashlib.sha256(k.encode()).hexdigest(): k for k in self.states}
            for key, state_dict in data.items():
                real = key if key in self.states else by_hash.get(key)
                if real in self.states:
                    self.states[real] = KeyState.from_dict(real, state_dict)
            logger.info("Restored state for %d keys from %s", len(data), self.state_file)
        except Exception as e:
            logger.warning("Failed to load key state: %s", e)

    def save_state(self) -> None:
        # Serialize concurrent callers (sweeper, state_saver task, shutdown) so
        # their tmp-write/rename sequences can't interleave within this process.
        with self._save_lock:
            try:
                data = {hashlib.sha256(k.encode()).hexdigest(): s.to_dict() for k, s in self.states.items()}
                tmp = self.state_file.with_suffix(".tmp")
                with open(tmp, "w") as f:
                    json.dump(data, f)
                tmp.replace(self.state_file)
                try:
                    os.chmod(self.state_file, 0o600)
                except OSError:
                    pass
                self._save_state_dirty = False
            except Exception as e:
                logger.warning("Failed to save key state: %s", e)

    # -- selection ---------------------------------------------------------

    def _weight_for_key(self, state: KeyState) -> float:
        """Higher weight = more likely to be picked. ACTIVE keys only."""
        weight = 1.0
        er = state.error_rate()
        weight *= (1.0 - er) * 2.0
        age = time.time() - state.last_used
        weight += min(age / 60.0, 1.0)
        return max(0.01, weight)

    def select_key(self, exclude: Optional[set[str]] = None) -> KeyState:
        """Select the best available key by strict priority.

        Priority: ACTIVE (weighted random) -> RESTING (LRU) -> COOLING (soonest).
        DISABLED keys are never returned. Raises NoAvailableKeyError if every
        key is DISABLED.
        """
        if not self.states:
            raise ValueError("No keys loaded. Call load_keys() first.")

        exclude = exclude or set()
        now = time.time()

        # Refresh states lazily
        for s in self.states.values():
            s.refresh_state()

        # 1. ACTIVE keys (weighted random)
        active = [
            s for k, s in self.states.items()
            if k not in exclude and s.state == ACTIVE
        ]
        if active:
            weights = [self._weight_for_key(s) for s in active]
            total = sum(weights)
            if total > 0:
                weights = [w / total for w in weights]
            return random.choices(active, weights=weights, k=1)[0]

        # 2. RESTING keys — least-recently-used (maximise spread)
        resting = [
            s for k, s in self.states.items()
            if k not in exclude and s.state == RESTING
        ]
        if resting:
            return max(resting, key=lambda s: now - s.last_used)

        # 3. COOLING keys — soonest available_at (no sleep; caller fires immediately)
        cooling = [
            s for k, s in self.states.items()
            if k not in exclude and s.state == COOLING
        ]
        if cooling:
            return min(cooling, key=lambda s: s.available_at)

        raise NoAvailableKeyError("No untried keys are available")

    # -- request dispatch --------------------------------------------------

    async def send_request(
        self,
        http_client,
        endpoint: str,
        payload: dict,
        prefer_key: Optional[str] = None,
    ) -> tuple[object, KeyState, bool]:
        """Send a request to Ollama Cloud with key rotation on failure.

        Uses a streaming HTTP request so the response body is not consumed
        until the caller iterates over it.

        Returns (httpx.Response, KeyState, was_retry).

        prefer_key pins the first attempt to a specific key (cache affinity);
        later attempts rotate as usual.

        Only definite connection-establishment failures and credential-specific
        authentication rejections rotate. HTTP 5xx, rate limits, and read
        failures are returned without replaying a possibly accepted generation.
        """
        tried: set[str] = set()
        attempts: list[dict] = []
        last_connect_error: Exception | None = None
        last_response: httpx.Response | None = None
        last_state: KeyState | None = None
        pin_exclude: Optional[set[str]] = None
        if prefer_key and prefer_key in self.states:
            pin_exclude = {k for k in self.states if k != prefer_key}

        for attempt in range(self.max_retries):
            try:
                if attempt == 0 and pin_exclude is not None:
                    try:
                        key_state = self.select_key(exclude=pin_exclude)
                    except NoAvailableKeyError:
                        key_state = self.select_key(exclude=tried)
                else:
                    key_state = self.select_key(exclude=tried)
            except NoAvailableKeyError:
                if last_response is not None and last_state is not None:
                    if attempts:
                        attempts.pop()
                    last_response.extensions["fuse_attempts"] = attempts
                    return last_response, last_state, bool(attempts)
                if last_connect_error is not None:
                    raise last_connect_error
                raise ConnectionError("No upstream keys available")

            key = key_state.key
            tried.add(key)

            # If the only key available is a not-yet-ready COOLING key (last-resort
            # selection when every key is cooling/excluded), firing immediately just
            # re-triggers its 429 and burns a retry slot. Wait while the wait is
            # short; hand back the cooldown when it is not.
            wait = key_state.available_at - time.time()
            if wait > 0:
                if wait > 5.0 and len(tried) < len(self.states):
                    tried.add(key)
                    continue
                await asyncio.sleep(min(wait, 0.5))

            req = http_client.build_request(
                "POST",
                endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                },
            )

            try:
                response = await http_client.send(req, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                backoff = self._escalate(self.net_base, self.net_cap, key_state.consecutive_errors + 1)
                key_state.mark_error(f"exception:{type(e).__name__}", backoff)
                self._save_state_dirty = True
                last_connect_error = e
                logger.warning("Connection error key=%s: %s", key_state.masked(), type(e).__name__)
                continue

            status = response.status_code

            model = str((payload or {}).get("model") or "")[:80]

            if status == 200:
                key_state.remember(status=200, model=model)
                key_state.mark_success(self.rest_seconds)
                self._save_state_dirty = True
                response.extensions["fuse_attempts"] = attempts
                return response, key_state, attempt > 0

            if status == 404:
                # Model not found — not a key problem. Treat as success for rotation.
                key_state.mark_success(self.rest_seconds)
                await response.aread()
                body_text = response.text
                await response.aclose()
                fake = httpx.Response(
                    status, content=body_text.encode(),
                    headers=response.headers,
                    request=req,
                )
                fake.extensions["fuse_attempts"] = attempts
                self._save_state_dirty = True
                return fake, key_state, attempt > 0

            # Error status — read body for classification + logging
            await response.aread()
            error_bytes = response.content
            error_text = response.text
            headers = response.headers
            await response.aclose()

            self._handle_error_status(status, headers, error_text, key_state, model=model)
            self._save_state_dirty = True

            fake = httpx.Response(
                status, content=error_bytes, headers=headers, request=req
            )
            last_response = fake
            last_state = key_state

            # Plan-gated model (402/403 "requires a subscription") is the same
            # for every unpaid key. Stop rotating — this is not a key failure.
            from .ollama_access import is_gated_error

            if is_gated_error(status, error_text):
                fake.extensions["fuse_attempts"] = attempts
                return fake, key_state, attempt > 0

            from .ollama_access import is_credential_auth_error, is_account_unavailable_error

            # Account-level flag ("Your account is currently unavailable") is
            # specific to this credential and nothing was generated — fail over
            # to the next key immediately.
            if is_account_unavailable_error(status, error_text):
                attempts.append({"credential_id": None, "status": status, "usage": {}})
                continue

            if is_credential_auth_error(status, error_text):
                attempts.append({"credential_id": None, "status": status, "usage": {}})
                continue

            if status == 429:
                # A 429 is never an accepted generation: the request was
                # rejected before any tokens were produced, so replaying it
                # on another key is safe and keeps the turn alive.
                attempts.append({"credential_id": None, "status": status, "usage": {}})
                continue

            # An HTTP response may represent an accepted generation. Never
            # replay it on another key without an idempotency contract.
            if status != 200:
                fake.extensions["fuse_attempts"] = attempts
                return fake, key_state, attempt > 0

        if last_response is not None and last_state is not None:
            if attempts:
                attempts.pop()
            last_response.extensions["fuse_attempts"] = attempts
            return last_response, last_state, True
        if last_connect_error is not None:
            raise last_connect_error
        raise ConnectionError("No upstream keys available")

    def _retry_after_capped(self, retry_after: Optional[float]) -> Optional[float]:
        """Retry-After, never trusted past the longest cooldown tier.

        A bogus/huge header must not park a key for months.
        """
        if retry_after is None:
            return None
        return min(retry_after, self.weekly_quota)

    def _handle_error_status(
        self,
        status: int,
        headers: httpx.Headers,
        body_text: str,
        key_state: KeyState,
        *,
        model: str = "",
    ) -> None:
        """Classify the error and apply the appropriate cooldown/disabled."""
        from .ollama_access import classify_cloud_error, is_credential_auth_error

        retry_after = parse_retry_after(headers.get("retry-after"))
        kind = classify_cloud_error(status, body_text)
        key_state.remember(status=status, body=body_text, model=model, error_type=kind)

        if kind == "model_gated":
            # Paid model, not a spent key. Count the request, stay ACTIVE.
            key_state.total_requests += 1
            logger.info("paid model key=%s model=%s body=%s", key_state.masked(), model, body_text[:200])
            return

        if kind == "bad_request":
            # Caller's fault — don't penalise the key.
            key_state.total_requests += 1
            key_state.total_errors += 1
            logger.error("HTTP 400 bad_request key=%s body=%s", key_state.masked(), body_text[:500])
            return

        if kind == "account_unavailable":
            # Ollama account-level temporary flag. Take the key fully out of
            # rotation for ~24h (re-applied on each recurrence, so persistently
            # flagged accounts stay out) — auto re-enables when it expires.
            logger.warning(
                "account unavailable key=%s body=%s", key_state.masked(), body_text[:200]
            )
            cooldown = self.account_unavailable
            capped = self._retry_after_capped(retry_after)
            if capped is not None and capped > cooldown:
                cooldown = capped
            key_state.available_at = time.time() + cooldown
            key_state.mark_error(
                "account_unavailable", cooldown, disable=True, disable_reason=DISABLED_UNAVAILABLE
            )
            return

        if kind == "weekly_quota":
            logger.warning("usage exhausted key=%s body=%s", key_state.masked(), body_text[:500])
            cooldown = self.weekly_quota
            capped = self._retry_after_capped(retry_after)
            if capped is not None and capped > cooldown:
                cooldown = capped
            key_state.available_at = time.time() + cooldown
            key_state.mark_error(
                "weekly_quota", cooldown, disable=True, disable_reason=DISABLED_WEEKLY
            )
            return

        if kind == "session_quota":
            logger.warning("session quota detected key=%s body=%s", key_state.masked(), body_text)
            cooldown = self.session_quota
            capped = self._retry_after_capped(retry_after)
            if capped is not None and capped > cooldown:
                cooldown = capped
            key_state.available_at = time.time() + cooldown
            key_state.mark_error(
                "session_quota", cooldown, disable=True, disable_reason=DISABLED_SESSION
            )
            return

        if kind == "hourly_quota":
            logger.warning("hourly quota detected key=%s body=%s", key_state.masked(), body_text)
            cooldown = self.hourly_quota
            capped = self._retry_after_capped(retry_after)
            if capped is not None and capped > cooldown:
                cooldown = capped
            key_state.mark_error("hourly_quota", cooldown)
            return

        if kind == "rate_limit":
            cooldown = max(self._retry_after_capped(retry_after) or 0.0, self.transient_429)
            if retry_after is not None and retry_after >= self.weekly_disable_threshold:
                # Retry-After at quota scale is a quota cooldown, not a blip.
                key_state.available_at = time.time() + cooldown
                key_state.mark_error(
                    "weekly_quota", cooldown, disable=True, disable_reason=DISABLED_WEEKLY
                )
            else:
                key_state.mark_error("rate_limit", cooldown)
            return

        if kind == "server" or status in (500, 502, 503, 504):
            backoff = self._escalate(self.five_xx_base, self.five_xx_cap, key_state.consecutive_errors + 1)
            key_state.mark_error(f"server_{status}", backoff)
            return

        if kind == "auth" and is_credential_auth_error(status, body_text):
            # Only a classified auth error whose status/body says the credential
            # is bad counts toward the auth-strike disable — a status that is
            # really a plan/quota denial must not permanently retire a healthy
            # key.
            key_state.consecutive_auth_errors += 1
            key_state.last_error_type = f"auth_{status}"
            key_state.last_error_at = time.time()
            if key_state.consecutive_auth_errors >= self.auth_disable_threshold:
                key_state.mark_error(
                    f"auth_{status}", 0.0,
                    disable=True, disable_reason=DISABLED_AUTH,
                )
                logger.warning(
                    "Key %s DISABLED after %d consecutive auth failures",
                    key_state.masked(), key_state.consecutive_auth_errors,
                )
            else:
                # Counters (total_requests/total_errors) increment exactly
                # once, inside mark_error; stay selectable with no cooldown.
                key_state.mark_error(f"auth_{status}", 0.0)
                logger.warning(
                    "Auth %d key=%s (%d/%d)",
                    status, key_state.masked(),
                    key_state.consecutive_auth_errors, self.auth_disable_threshold,
                )
            return

        key_state.mark_error(f"http_{status}", self.other_4xx)

    def _escalate(self, base: float, cap: float, n: int) -> float:
        """Exponential backoff: base * 2^(n-1), capped."""
        return min(base * (2.0 ** (n - 1)), cap)

    # -- background sweeper -------------------------------------------------

    async def sweeper_loop(self) -> None:
        """Periodically expire rests/cooldowns, decay errors, and persist state."""
        while True:
            try:
                await asyncio.sleep(self.sweeper_interval)
            except asyncio.CancelledError:
                break
            try:
                changed = False
                for s in self.states.values():
                    prev = s.state
                    s.refresh_state()
                    if s.state != prev:
                        changed = True
                    if s.decay_errors(self.decay_factor):
                        changed = True
                if changed or self._save_state_dirty:
                    await asyncio.to_thread(self.save_state)
            except Exception as e:
                logger.warning("Sweeper error: %s", e)

    def start_sweeper(self) -> None:
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self.sweeper_loop())

    async def stop_sweeper(self) -> None:
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            self._sweeper_task = None

    # -- introspection -----------------------------------------------------

    def get_stats(self) -> dict:
        total_keys = len(self.states)
        active = sum(1 for s in self.states.values() if s.state == ACTIVE)
        resting = sum(1 for s in self.states.values() if s.state == RESTING)
        cooling = sum(1 for s in self.states.values() if s.state == COOLING)
        disabled = sum(1 for s in self.states.values() if s.state == DISABLED)
        total_req = sum(s.total_requests for s in self.states.values())
        total_err = sum(s.total_errors for s in self.states.values())
        return {
            "total_keys": total_keys,
            "active_keys": active,
            "resting_keys": resting,
            "in_cooldown": cooling,
            "disabled_keys": disabled,
            "working_keys": total_keys - disabled,
            "total_requests": total_req,
            "total_errors": total_err,
            "pool_error_rate": round(total_err / max(1, total_req), 4),
        }

    def get_key_details(self) -> list[dict]:
        return [
            {
                "key": s.key[:16] + "...",
                "state": s.state,
                "disabled_reason": s.disabled_reason,
                "total_requests": s.total_requests,
                "total_errors": s.total_errors,
                "error_rate": round(s.error_rate() * 100, 2),
                "available_in": round(s.available_in(), 2),
                "rest_remaining": round(s.rest_remaining(), 2),
                "last_error": s.last_error_type,
                "consecutive_errors": s.consecutive_errors,
                "consecutive_auth_errors": s.consecutive_auth_errors,
            }
            for s in self.states.values()
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_pool: Optional[KeyPool] = None


def get_pool(
    keys_file: str = "ollama-keys.txt",
    state_file: str = "key_state.json",
    max_retries: int = DEFAULTS["max_retries"],
    rest_seconds: float = DEFAULTS["rest_seconds"],
    transient_429: float = DEFAULTS["transient_429"],
    hourly_quota: float = DEFAULTS["hourly_quota"],
    session_quota: float = DEFAULTS["session_quota"],
    weekly_quota: float = DEFAULTS["weekly_quota"],
    account_unavailable: float = DEFAULTS["account_unavailable"],
    weekly_disable_threshold: float = DEFAULTS["weekly_disable_threshold"],
    five_xx_base: float = DEFAULTS["5xx_base"],
    five_xx_cap: float = DEFAULTS["5xx_cap"],
    net_base: float = DEFAULTS["net_base"],
    net_cap: float = DEFAULTS["net_cap"],
    other_4xx: float = DEFAULTS["other_4xx"],
    auth_disable_threshold: int = DEFAULTS["auth_disable_threshold"],
    decay_factor: float = DEFAULTS["decay_factor"],
    sweeper_interval: float = DEFAULTS["sweeper_interval"],
) -> KeyPool:
    global _pool
    if _pool is None:
        _pool = KeyPool(
            keys_file=keys_file,
            state_file=state_file,
            max_retries=max_retries,
            rest_seconds=rest_seconds,
            transient_429=transient_429,
            hourly_quota=hourly_quota,
            session_quota=session_quota,
            weekly_quota=weekly_quota,
            account_unavailable=account_unavailable,
            weekly_disable_threshold=weekly_disable_threshold,
            five_xx_base=five_xx_base,
            five_xx_cap=five_xx_cap,
            net_base=net_base,
            net_cap=net_cap,
            other_4xx=other_4xx,
            auth_disable_threshold=auth_disable_threshold,
            decay_factor=decay_factor,
            sweeper_interval=sweeper_interval,
        )
    return _pool


def reset_pool() -> None:
    global _pool
    _pool = None
