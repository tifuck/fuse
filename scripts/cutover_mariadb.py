"""Import the final stopped-service snapshot and atomically select MariaDB.

The target schema must already exist and be empty. Does not start/stop Fuse.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit, urlunsplit

from app.config import settings, ROOT
from scripts.migrate_mariadb import migrate


def cutover(database):
    state = subprocess.check_output(["systemctl", "show", "fuse", "-p", "ActiveState", "--value"], text=True).strip()
    if state not in ("inactive", "failed"):
        raise RuntimeError("Stop fuse.service before taking the final migration snapshot")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = ROOT / "backups" / ("mariadb-cutover-" + stamp)
    root.mkdir(parents=True, mode=0o700)
    shutil.copy2(ROOT / ".env", root / "env.before")
    snapshot = root / "fuse.db"
    fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    src = sqlite3.connect(Path(settings.database_path).resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(snapshot)
    src.backup(dst)
    dst.close()
    src.close()
    with snapshot.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    (root / "snapshot.sha256").write_text(checksum + "  fuse.db\n")
    report = migrate(snapshot, database)
    (root / "migration-report.json").write_text(json.dumps(report, indent=2))
    url = urlunsplit(urlsplit(settings.database_url)._replace(path="/" + database))
    env = (ROOT / ".env").read_text()
    env = re.sub(r"(?m)^DATABASE_URL=.*$", "DATABASE_URL=" + url, env)
    env = re.sub(r"(?m)^DATABASE_MARIADB_ENABLED=.*\n?", "", env)
    env = env.rstrip() + "\nDATABASE_MARIADB_ENABLED=true\n"
    temporary = ROOT / ".env.mariadb.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(env)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, ROOT / ".env")
    print(f"Verified migration and selected MariaDB. Backup: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    cutover(parser.parse_args().database)
