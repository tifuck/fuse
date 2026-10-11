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
import threading
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
    state = subprocess.check_output(
        ["systemctl", "show", "fuse-dev", "-p", "ActiveState", "--value"], text=True
    ).strip()
    if state not in ("inactive", "failed"):
        raise SystemExit("refusing: fuse-dev.service is active; stop it first: systemctl stop fuse-dev")
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

        def drop_target():
            subprocess.run([*base, "-e", f"DROP DATABASE IF EXISTS `{TARGET}`"], check=False)

        subprocess.run(
            [*base, "-e", f"DROP DATABASE IF EXISTS `{TARGET}`; CREATE DATABASE `{TARGET}`"],
            check=True,
        )
        try:
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
                    # Same as backup_mariadb: chat_runs rows exceed the default
                    # packet, and the server drops the dump mid-table (2013).
                    "--max-allowed-packet=1G",
                    SOURCE,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            drop_target()
            raise SystemExit("clone failed: mariadb-dump not found; fuse_dev dropped")
        # Drain dump's stderr concurrently: unread, a full pipe would block the
        # dump, stall its stdout, and deadlock the pipeline below.
        dump_err = []
        reader = threading.Thread(target=lambda: dump_err.append(dump.stderr.read()), daemon=True)
        reader.start()
        load = subprocess.Popen([*base, "--max-allowed-packet=1G", TARGET], stdin=dump.stdout, stderr=subprocess.PIPE)
        dump.stdout.close()
        load_err = load.communicate()[1]
        dump_status = dump.wait()
        reader.join()
        if load.returncode or dump_status:
            drop_target()
            detail = ((dump_err[0] if dump_err else b"") or load_err or b"").decode(errors="replace").strip()
            raise SystemExit("clone failed; fuse_dev dropped" + (f": {detail}" if detail else ""))
        check = subprocess.run(
            [*base, TARGET, "-e", "SELECT `value` FROM schema_meta WHERE `key`='fuse_mariadb_v1'"],
            check=True,
            capture_output=True,
            text=True,
        )
        if not check.stdout.strip():
            drop_target()
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
