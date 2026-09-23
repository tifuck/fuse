"""5-field Unix cron parser. Stdlib only. Numeric fields; 0=Sunday."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CronError = ValueError

_FIELD_RANGES = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # dom
    (1, 12),  # month
    (0, 6),   # dow, 0=Sunday
)


def parse_tz(name: str) -> ZoneInfo:
    text = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, KeyError, TypeError) as exc:
        raise CronError(f"unknown timezone {text!r}") from exc


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    raw = (expr or "").strip()
    parts = raw.split()
    if len(parts) != 5:
        raise CronError("cron must have 5 fields (minute hour dom month dow)")
    out = []
    for i, part in enumerate(parts):
        lo, hi = _FIELD_RANGES[i]
        out.append(_parse_field(part, lo, hi))
    return tuple(out)  # type: ignore[return-value]


def _parse_field(part: str, lo: int, hi: int) -> set[int]:
    text = (part or "").strip()
    if not text:
        raise CronError("empty cron field")
    if any(ch.isalpha() for ch in text):
        raise CronError("cron names (MON, JAN) are not supported")
    values: set[int] = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError("empty cron list item")
        step = 1
        if "/" in chunk:
            base, step_s = chunk.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError("invalid cron step")
            step = int(step_s)
            chunk = base or "*"
        if chunk == "*":
            start, end = lo, hi
        elif "-" in chunk:
            a, b = chunk.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                raise CronError("invalid cron range")
            start, end = int(a), int(b)
        else:
            if not chunk.isdigit():
                raise CronError("invalid cron value")
            start = end = int(chunk)
        if start > end or start < lo or end > hi:
            raise CronError("cron value out of range")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError("cron field matched nothing")
    return values


def compile_daily(hour: int, minute: int) -> str:
    _check_hm(hour, minute)
    return f"{minute} {hour} * * *"


def compile_weekly(hour: int, minute: int, weekdays: int) -> str:
    """weekdays bitmask: Monday=bit0 … Sunday=bit6. Cron dow 0=Sunday."""
    _check_hm(hour, minute)
    if not (1 <= int(weekdays) <= 127):
        raise CronError("select at least one weekday")
    cron_days = []
    for bit in range(7):
        if weekdays & (1 << bit):
            cron_days.append(str((bit + 1) % 7))  # Mon=1 … Sun=0
    cron_days = sorted(cron_days, key=lambda s: (s != "0", s))
    return f"{minute} {hour} * * {','.join(cron_days)}"


def parse_hhmm(value: str) -> tuple[int, int]:
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise CronError("time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    _check_hm(hour, minute)
    return hour, minute


def _check_hm(hour: int, minute: int) -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CronError("time must be HH:MM")


def next_fire(expr: str, tz_name: str, after_utc: datetime) -> datetime | None:
    """Next UTC instant strictly after after_utc that matches expr in tz_name."""
    minute, hour, dom, month, dow = parse_cron(expr)
    tz = parse_tz(tz_name)
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    local = after_utc.astimezone(tz)
    cursor = (local.replace(second=0, microsecond=0) + timedelta(minutes=1))
    limit = cursor + timedelta(days=400)
    while cursor <= limit:
        if (
            cursor.minute in minute
            and cursor.hour in hour
            and cursor.month in month
            and cursor.day in dom
            and (cursor.isoweekday() % 7) in dow
        ):
            try:
                aware = datetime(
                    cursor.year, cursor.month, cursor.day, cursor.hour, cursor.minute,
                    tzinfo=tz, fold=0,
                )
            except Exception:
                cursor += timedelta(minutes=1)
                continue
            if aware.strftime("%Y%m%d%H%M") != cursor.strftime("%Y%m%d%H%M"):
                cursor += timedelta(minutes=1)
                continue
            utc = aware.astimezone(timezone.utc)
            if utc > after_utc:
                return utc
        cursor += timedelta(minutes=1)
    return None


def next_n(expr: str, tz_name: str, after_utc: datetime, n: int = 5) -> list[datetime]:
    out: list[datetime] = []
    cursor = after_utc
    for _ in range(max(1, min(n, 24))):
        nxt = next_fire(expr, tz_name, cursor)
        if nxt is None:
            break
        out.append(nxt)
        cursor = nxt
    return out


def implied_min_gap(expr: str, tz_name: str, after_utc: datetime, samples: int = 12) -> int | None:
    times = next_n(expr, tz_name, after_utc, samples)
    if len(times) < 2:
        return None
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    if not gaps:
        return None
    return int(min(gaps))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
