#!/usr/bin/env python3
"""Read-only investigation tool for failed dashboard tool calls (the
tool_failures table in the configured MariaDB schema, see app/tool_failures.py).

Run from the tree whose schema you mean (/root/fuse = fuse_live,
/root/fuse-dev = fuse_dev); re-execs through ./venv when needed:

    ./venv/bin/python scripts/tool_failures.py                    # summary, last 7 days
    ./venv/bin/python scripts/tool_failures.py summary --hours 24
    ./venv/bin/python scripts/tool_failures.py groups --tool run_terminal_command
    ./venv/bin/python scripts/tool_failures.py list --kind unknown_tool --model grok-4
    ./venv/bin/python scripts/tool_failures.py list --grep "permission denied" --limit 50
    ./venv/bin/python scripts/tool_failures.py show 3860           # full row: args, trace, reasoning
    ./venv/bin/python scripts/tool_failures.py show 3860 --json
    ./venv/bin/python scripts/tool_failures.py export --hours 72 > pack.json   # LLM analysis pack

Filters (summary, groups, list, export):
    --hours N        window (default 168; 0 = all time)
    --kind K         error_kind (see app/tool_failures.KINDS)
    --tool NAME      tool_name
    --model TEXT     substring of model or upstream_model
    --source TYPE    source_type (claude, chatgpt, grok, ...)
    --user ID        user_id
    --thread ID      thread_id
    --grep TEXT      substring of error, arguments or exception (case-insensitive)
    --since ID       only rows with id > ID (e.g. failures since a fix landed)

`groups` clusters rows by (tool, kind, first 160 chars of error) and lists
example ids to `show`. `list` and `groups` take --limit and --json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# One connection is plenty; the services share MariaDB's max_connections.
os.environ.setdefault("DATABASE_POOL_SIZE", "1")

try:
    from app import db, tool_failures
except ModuleNotFoundError:
    venv = ROOT / "venv" / "bin" / "python"
    if venv.exists():
        os.execv(str(venv), [str(venv), *sys.argv])
    raise

WIDTH = 100


def _where(args) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if getattr(args, "hours", 0):
        clauses.append("created_at >= ?")
        params.append(tool_failures._hours_ago(args.hours))
    for column, value in (("error_kind", args.kind), ("tool_name", args.tool), ("source_type", args.source)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if args.model:
        clauses.append("(model LIKE ? OR upstream_model LIKE ?)")
        params += [f"%{args.model}%"] * 2
    if args.user is not None:
        clauses.append("user_id = ?")
        params.append(args.user)
    if args.thread is not None:
        clauses.append("thread_id = ?")
        params.append(args.thread)
    if args.grep:
        clauses.append("(LOWER(error) LIKE ? OR LOWER(arguments) LIKE ? OR LOWER(exception) LIKE ?)")
        params += [f"%{args.grep.lower()}%"] * 3
    if args.since is not None:
        clauses.append("id > ?")
        params.append(args.since)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _one_line(value, width: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _window(args) -> str:
    return f"last {args.hours}h" if args.hours else "all time"


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _counts(column: str, where: str, params: list, limit: int = 15) -> list[tuple[str, int]]:
    rows = db.fetchall(
        f"SELECT {column} AS k, COUNT(*) AS n FROM tool_failures {where} "
        f"GROUP BY {column} ORDER BY n DESC LIMIT {int(limit)}",
        tuple(params),
    )
    return [(str(r["k"] or "-"), int(r["n"])) for r in rows]


def _groups(where: str, params: list, limit: int) -> list[dict]:
    rows = db.fetchall(
        f"""SELECT tool_name, error_kind, MIN(error) AS error, COUNT(*) AS n,
                   MIN(created_at) AS first_seen, MAX(created_at) AS last_seen,
                   COUNT(DISTINCT model) AS models, MAX(id) AS latest_id,
                   GROUP_CONCAT(DISTINCT model) AS model_list
            FROM tool_failures {where}
            GROUP BY tool_name, error_kind, substr(error, 1, 160)
            ORDER BY n DESC, latest_id DESC LIMIT {int(limit)}""",
        tuple(params),
    )
    return [dict(r) for r in rows]


def cmd_summary(args) -> int:
    where, params = _where(args)
    row = db.fetchone(
        f"SELECT COUNT(*) AS n, MIN(created_at) AS first, MAX(created_at) AS last, "
        f"MIN(id) AS lo, MAX(id) AS hi FROM tool_failures {where}",
        tuple(params),
    )
    total = int(row["n"]) if row else 0
    sections = {
        "kind": _counts("error_kind", where, params),
        "tool": _counts("tool_name", where, params),
        "model": _counts("model", where, params),
        "source": _counts("source_type", where, params),
    }
    groups = _groups(where, params, args.limit)
    if args.json:
        _dump({"window": _window(args), "total": total, "first": row["first"] if total else None,
               "last": row["last"] if total else None, **{f"by_{k}": dict(v) for k, v in sections.items()},
               "groups": groups})
        return 0
    print(f"{total} failed tool calls ({_window(args)})")
    if not total:
        return 0
    print(f"ids {row['lo']}–{row['hi']}, {row['first']} → {row['last']}\n")
    for title, counts in sections.items():
        print(f"by {title}:")
        for key, n in counts:
            print(f"  {n:>5}  {key}")
        print()
    _print_groups(groups, "top failure groups")
    return 0


def _print_groups(groups: list[dict], title: str) -> None:
    print(f"{title}:")
    for g in groups:
        print(f"  {g['n']:>5}x  {g['tool_name'] or '-'}  [{g['error_kind']}]  "
              f"{g['first_seen'][:16]} → {g['last_seen'][:16]}  latest #{g['latest_id']}")
        print(f"         {_one_line(g['error'], WIDTH - 9)}")
        print(f"         models: {_one_line(g['model_list'], WIDTH - 17)}")


def cmd_groups(args) -> int:
    where, params = _where(args)
    groups = _groups(where, params, args.limit)
    if args.json:
        _dump(groups)
        return 0
    if not groups:
        print(f"no failed tool calls ({_window(args)})")
        return 0
    _print_groups(groups, f"failure groups ({_window(args)})")
    return 0


def cmd_list(args) -> int:
    where, params = _where(args)
    rows = db.fetchall(
        f"SELECT id, created_at, model, source_type, tool_name, error_kind, error, arguments "
        f"FROM tool_failures {where} ORDER BY id DESC LIMIT {int(args.limit)}",
        tuple(params),
    )
    if args.json:
        _dump([dict(r) for r in rows])
        return 0
    if not rows:
        print(f"no failed tool calls ({_window(args)})")
        return 0
    for r in rows:
        print(f"#{r['id']}  {r['created_at'][:19]}  {r['model'] or '-'}  {r['tool_name'] or '-'}  [{r['error_kind']}]")
        print(f"    error: {_one_line(r['error'], WIDTH - 11)}")
        print(f"    args:  {_one_line(r['arguments'], WIDTH - 11)}")
    print(f"\n{len(rows)} shown (newest first); `show <id>` for the full row")
    return 0


def _block(label: str, value) -> None:
    if value in (None, "", {}, []):
        return
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    print(f"\n── {label} " + "─" * max(0, WIDTH - len(label) - 4))
    print(value)  # unwrapped, so arguments and commands copy-paste intact


def cmd_show(args) -> int:
    row = db.fetchone("SELECT * FROM tool_failures WHERE id = ?", (args.id,))
    if not row:
        print(f"no tool failure #{args.id}", file=sys.stderr)
        return 1
    view = tool_failures._row_view(row)
    try:
        view["arguments"] = json.loads(view["arguments"])
    except (TypeError, ValueError):
        pass
    if args.json:
        _dump(view)
        return 0
    print(f"#{view['id']}  {view['created_at']}  [{view['error_kind']}]  {view['tool_name'] or '-'}")
    print(f"model {view['model'] or '-'}  (source {view['source_type'] or '-'}, upstream {view['upstream_model'] or '-'})")
    print(f"user {view['user_id']}  thread {view['thread_id']}  run {view['run_id'] or '-'}  "
          f"round {view['round_index']}  phase {view['phase'] or '-'}  call {view['call_id'] or '-'}  "
          f"{view['latency_ms']} ms")
    _block("error", view["error"])
    _block("arguments", view["arguments"])
    _block("exception", view["exception"])
    _block("trace", view["trace"])
    _block("reasoning before the call", view["reasoning"])
    _block("user request", view["user_text"])
    return 0


def cmd_export(args) -> int:
    where, params = _where(args)
    rows = db.fetchall(
        f"SELECT * FROM tool_failures {where} ORDER BY id DESC LIMIT {int(args.limit)}",
        tuple(params),
    )
    _dump({
        "guide": tool_failures.GUIDE,
        "generated_at": db.now(),
        "window": _window(args),
        "groups": _groups(where, params, 200),
        "recent": [tool_failures._row_view(r) for r in rows],
    })
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool_failures.py",
        description="Inspect failed dashboard tool calls.",
        epilog="Run with -h after a command for its options; see the module docstring for examples.",
    )
    sub = parser.add_subparsers(dest="command")

    def filtered(name: str, help_text: str, limit: int) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--hours", type=int, default=168, help="window in hours (0 = all time)")
        p.add_argument("--kind", default="")
        p.add_argument("--tool", default="")
        p.add_argument("--model", default="", help="substring of model or upstream_model")
        p.add_argument("--source", default="", help="source_type")
        p.add_argument("--user", type=int)
        p.add_argument("--thread", type=int)
        p.add_argument("--grep", default="", help="substring of error/arguments/exception")
        p.add_argument("--since", type=int, help="only ids greater than this")
        p.add_argument("--limit", type=int, default=limit)
        p.add_argument("--json", action="store_true")
        return p

    filtered("summary", "counts by kind/tool/model/source and top groups", 10)
    filtered("groups", "cluster failures by tool, kind and error text", 40)
    filtered("list", "one entry per failure, newest first", 30)
    filtered("export", "JSON analysis pack (guide + groups + full rows)", 200)
    show = sub.add_parser("show", help="the full row for one failure")
    show.add_argument("id", type=int)
    show.add_argument("--json", action="store_true")
    return parser


COMMANDS = {"summary": cmd_summary, "groups": cmd_groups, "list": cmd_list, "show": cmd_show, "export": cmd_export}


def main(argv: list[str]) -> int:
    parser = _parser()
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["summary", *argv]  # bare filters mean a summary
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
