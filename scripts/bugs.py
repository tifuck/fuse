#!/usr/bin/env python3
"""Review queue for user-submitted bug reports (bug_reports in data/fuse.db).

Run from the repo root (re-execs through ./venv when needed):

    ./venv/bin/python scripts/bugs.py list
    ./venv/bin/python scripts/bugs.py show F-abc123
    ./venv/bin/python scripts/bugs.py set F-abc123 confirmed
    ./venv/bin/python scripts/bugs.py next F-abc123        # advance one step
    ./venv/bin/python scripts/bugs.py set F-abc123 rejected --force
    ./venv/bin/python scripts/bugs.py delete F-abc123

Lifecycle is sequential, mirroring app/issues.py STATUSES:

    new -> triaging -> triaged -> confirmed -> planned -> fixing -> fixed -> released

`triaging` is owned by the auto-triage pipeline. Off-ramps need_info /
duplicate / rejected can be taken from any main-path status and are terminal
unless --force is passed, as is any backward move.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from app import db, issues
except ModuleNotFoundError:
    venv = ROOT / "venv" / "bin" / "python"
    if venv.exists():
        import os

        os.execv(str(venv), [str(venv), *sys.argv])
    raise

MAIN_PATH = ("new", "triaging", "triaged", "confirmed", "planned", "fixing", "fixed", "released")
SIDES = ("need_info", "duplicate", "rejected")
TERMINAL = ("released", "duplicate", "rejected")
OPEN = ("new", "triaging", "triaged", "need_info", "confirmed", "planned", "fixing", "fixed")

SUMMARY_WIDTH = 88


def _retry(fn, attempts: int = 4, delay: float = 1.5):
    """The live service holds the DB; WAL keeps writes brief but contended."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:  # sqlite3.OperationalError: database is locked
            last = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(delay)
    raise last  # type: ignore[misc]


def _resolve(ref: str) -> dict | None:
    return issues.get_by_ref(ref)


def _transition_allowed(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in SIDES or current in TERMINAL:
        return False  # off-ramps are terminal; re-entry needs --force
    if target in SIDES:
        return True  # off-ramp from any main-path status
    if current in MAIN_PATH and target in MAIN_PATH:
        return MAIN_PATH.index(target) == MAIN_PATH.index(current) + 1
    return False


def _set_status(ref: str, target: str, *, force: bool = False) -> int:
    if target not in issues.STATUSES:
        print(
            f"unknown status '{target}'. valid: {', '.join(issues.STATUSES)}",
            file=sys.stderr,
        )
        return 2

    def apply() -> str:
        with db.tx() as conn:
            row = conn.execute("SELECT id, status FROM bug_reports WHERE ref=?", (ref,)).fetchone()
            if row is None:
                return "missing"
            current = row["status"]
            if current == target:
                return f"same:{current}"
            if not force and not _transition_allowed(current, target):
                return (
                    f"refused: {ref} {current} -> {target} breaks the sequence "
                    f"({' -> '.join(MAIN_PATH)}); off-ramps: {', '.join(SIDES)}. "
                    "Pass --force to override."
                )
            cur = conn.execute(
                "UPDATE bug_reports SET status=?, updated_at=? WHERE id=?",
                (target, db.now(), row["id"]),
            )
            return "ok" if cur.rowcount > 0 else "missing"

    result = _retry(apply)
    if result == "ok":
        print(f"{ref} {target}")
        return 0
    if result.startswith("same:"):
        print(f"{ref} already {result.split(':', 1)[1]}")
        return 0
    if result == "missing":
        print(f"no report {ref}", file=sys.stderr)
        return 1
    print(result, file=sys.stderr)
    return 3


def _fmt(row, index: int) -> str:
    summary = (row["summary"] or "").replace("\n", " ")
    if len(summary) > SUMMARY_WIDTH:
        summary = summary[: SUMMARY_WIDTH - 1] + "…"
    age = (row["created_at"] or "")[:10]
    page = row["page"] or "-"
    return f"{index:>3}  {row['ref']}  {age}  {row['status']:<10} {page:<28} {summary}"


def cmd_list(args: list[str]) -> int:
    show_all = "--all" in args or "-a" in args
    as_json = "--json" in args

    def query():
        if show_all:
            return issues.recent(limit=500)
        clauses = ",".join("?" for _ in OPEN)
        rows = db.fetchall(
            f"SELECT * FROM bug_reports WHERE status IN ({clauses}) ORDER BY id DESC LIMIT 500",
            OPEN,
        )
        return [issues._row_view(r) for r in rows]

    rows = _retry(query)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if not rows:
        print("no bug reports" + ("" if show_all else " (use --all to include released/duplicate/rejected)"))
        return 0
    print(f"{'#':>3}  {'ref':<9} {'date':<10} {'status':<10} {'page':<28} summary")
    for i, row in enumerate(rows, 1):
        print(_fmt(row, i))
    return 0


def cmd_show(args: list[str]) -> int:
    if not args or args[0].startswith("-"):
        print("usage: show <ref>", file=sys.stderr)
        return 2
    as_json = "--json" in args[1:]
    ref = args[0]
    row = _retry(lambda: _resolve(ref))
    if row is None:
        print(f"no report {ref}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        return 0
    for key in ("id", "ref", "status", "attempts", "created_at", "updated_at", "page", "view", "user_agent"):
        print(f"{key}: {row.get(key, '')}")
    print(f"summary: {row.get('summary', '')}")
    print(f"expected: {row.get('expected', '')}")
    context = row.get("context")
    if context:
        print("context: " + json.dumps(context, ensure_ascii=False, indent=2, default=str))
    triage = row.get("triage")
    if triage:
        print("triage: " + json.dumps(triage, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_set(args: list[str]) -> int:
    positional = [a for a in args if not a.startswith("-")]
    force = "--force" in args or "-f" in args
    if len(positional) != 2:
        print("usage: set <ref> <status>", file=sys.stderr)
        return 2
    return _set_status(positional[0], positional[1], force=force)


def cmd_next(args: list[str]) -> int:
    positional = [a for a in args if not a.startswith("-")]
    if len(positional) != 1:
        print("usage: next <ref>", file=sys.stderr)
        return 2
    ref = positional[0]
    row = _retry(lambda: _resolve(ref))
    if row is None:
        print(f"no report {ref}", file=sys.stderr)
        return 1
    current = row["status"]
    if current not in MAIN_PATH:
        print(f"{ref} is off the main path ({current}); use 'set' to move it explicitly", file=sys.stderr)
        return 3
    idx = MAIN_PATH.index(current)
    if idx + 1 >= len(MAIN_PATH):
        print(f"{ref} already {current} (end of the path)")
        return 0
    return _set_status(ref, MAIN_PATH[idx + 1])


def cmd_delete(args: list[str]) -> int:
    positional = [a for a in args if not a.startswith("-")]
    if len(positional) != 1:
        print("usage: delete <ref>", file=sys.stderr)
        return 2
    ref = positional[0]

    def apply() -> int:
        with db.tx() as conn:
            cur = conn.execute("DELETE FROM bug_reports WHERE ref=?", (ref,))
            return cur.rowcount

    deleted = _retry(apply)
    if deleted:
        print(f"deleted {ref}")
        return 0
    print(f"no report {ref}", file=sys.stderr)
    return 1


COMMANDS = {
    "list": cmd_list,
    "show": cmd_show,
    "set": cmd_set,
    "next": cmd_next,
    "delete": cmd_delete,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    handler = COMMANDS.get(argv[0])
    if handler is None:
        print(f"unknown command '{argv[0]}'. commands: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
