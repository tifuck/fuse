"""Compile and EXPLAIN static application SQL against an imported test schema."""
import ast
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.mysql_compat import connect, _rewrite


def audit(database="fuse_stage"):
    url = urlsplit(settings.database_url)
    conn = connect(urlunsplit(url._replace(path="/" + database)))
    count, errors, dynamic = 0, [], []
    for path in sorted(Path("app").rglob("*.py")):
        if path.name == "mysql_compat.py":
            continue
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                first = node.values[0] if node.values else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE)\b", first.value):
                    dynamic.append(f"{path}:{node.lineno}")
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            sql = node.value.strip()
            if not re.match(r"(SELECT\s+.+\s+FROM\s|INSERT\s+(?:OR IGNORE\s+)?INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)", sql, re.I | re.S):
                continue
            if any(isinstance(parent, ast.JoinedStr) and node in parent.values for parent in ast.walk(tree)):
                continue
            if "sqlite_master" in sql or "sqlite_sequence" in sql:
                continue
            if isinstance(parents.get(node), ast.BinOp):
                dynamic.append(f"{path}:{node.lineno}")
                continue
            try:
                query = _rewrite(sql)
                params = tuple(1 for _ in re.findall(r"(?<!%)%s", query))
                conn.native("EXPLAIN " + query, params)
                count += 1
            except Exception as exc:
                errors.append((str(path), node.lineno, type(exc).__name__, str(exc)))
    conn.close()
    print(f"Explained {count} static SQL statements; {len(dynamic)} dynamic fragments require workflow coverage")
    for item in errors:
        print(item)
    return errors


if __name__ == "__main__":
    raise SystemExit(bool(audit()))
