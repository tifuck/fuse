"""Replace the fuse_dev schema with a consistent copy of fuse_live.

Usage: python -m scripts.clone_db_to_dev
Credentials come from the production DATABASE_URL. The target name is fixed;
this never writes to fuse_live. Stop fuse-dev before running it. Afterwards it
re-creates the permanent QA login (scripts.dev_qa_account) from the dev tree.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import settings

SOURCE = "fuse_live"
TARGET = "fuse_dev"
DEV_TREE = Path("/root/fuse-dev")
QA_SCRIPT = DEV_TREE / "scripts" / "dev_qa_account.py"


def quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def main():
    parsed = urlsplit(settings.database_url)
    if parsed.scheme != "mysql" or parsed.path.strip("/") != SOURCE:
        raise SystemExit("refusing: DATABASE_URL must point at fuse_live")
    if not re.fullmatch(r"[A-Za-z0-9_]+", TARGET):
        raise SystemExit("refusing: bad target name")
    config = "[client]\n" + "\n".join(
        f"{key}={quote(value)}"
        for key, value in {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
        }.items()
    )
    with tempfile.NamedTemporaryFile(mode="w", prefix="fuse-clone-", suffix=".cnf") as auth:
        auth.write(config)
        auth.flush()
        os.chmod(auth.name, 0o600)
        base = ["mariadb", f"--defaults-extra-file={auth.name}", "--batch", "--skip-column-names"]
        subprocess.run(
            [*base, "-e", f"DROP DATABASE IF EXISTS `{TARGET}`; CREATE DATABASE `{TARGET}`"],
            check=True,
        )
        dump = subprocess.Popen(
            [
                "mariadb-dump",
                f"--defaults-extra-file={auth.name}",
                "--single-transaction",
                "--quick",
                "--hex-blob",
                "--routines",
                "--triggers",
                "--skip-lock-tables",
                SOURCE,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        load = subprocess.Popen([*base, TARGET], stdin=dump.stdout, stderr=subprocess.PIPE)
        dump.stdout.close()
        load_err = load.communicate()[1]
        dump_err = dump.stderr.read()
        if load.returncode or dump.wait():
            subprocess.run([*base, "-e", f"DROP DATABASE IF EXISTS `{TARGET}`"], check=False)
            detail = (dump_err or load_err or b"").decode(errors="replace").strip()
            raise SystemExit("clone failed; fuse_dev dropped" + (f": {detail}" if detail else ""))
        check = subprocess.run(
            [*base, TARGET, "-e", "SELECT `value` FROM schema_meta WHERE `key`='fuse_mariadb_v1'"],
            check=True,
            capture_output=True,
            text=True,
        )
        if not check.stdout.strip():
            raise SystemExit("clone failed: schema_meta stamp missing; fuse_dev dropped")
    print(f"cloned {SOURCE} -> {TARGET}")
    # The clone replaces fuse_dev wholesale; restore the permanent QA login and
    # its fixture chats from the dev tree (password persists in its data/).
    if QA_SCRIPT.exists():
        result = subprocess.run(
            [str(DEV_TREE / "venv" / "bin" / "python"), "-m", "scripts.dev_qa_account"], cwd=DEV_TREE, check=False
        )
        if result.returncode:
            print("warning: QA account reseed failed; run scripts.dev_qa_account from /root/fuse-dev", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
