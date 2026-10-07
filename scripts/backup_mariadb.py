"""Consistent MariaDB backup; credentials never appear in process arguments."""
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote

from app.config import settings


def backup(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"fuse-mariadb-{stamp}.sql.gz"
    partial = target.with_suffix(".partial")
    parsed = urlsplit(settings.database_url)
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    config = "[client]\n" + "\n".join(f"{k}={quote(str(v))}" for k, v in {
        "host": parsed.hostname or "127.0.0.1", "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
    }.items())
    with tempfile.NamedTemporaryFile(mode="w", prefix="fuse-db-", suffix=".cnf") as auth:
        auth.write(config)
        auth.flush()
        proc = subprocess.Popen(["mariadb-dump", f"--defaults-extra-file={auth.name}",
            "--single-transaction", "--quick", "--hex-blob", "--skip-lock-tables", parsed.path.strip("/")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain stderr concurrently: unread, a full pipe would block the dump
        # mid-stream and stall the stdout copy loop below.
        stderr_buf = []
        drain = threading.Thread(target=lambda: stderr_buf.append(proc.stderr.read()), daemon=True)
        drain.start()
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as stream:
                while block := proc.stdout.read(1024 * 1024):
                    stream.write(block)
            status = proc.wait()
            drain.join()
            if status:
                raise RuntimeError("MariaDB dump failed: " + stderr_buf[0].decode(errors="replace"))
            partial.rename(target)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            partial.unlink(missing_ok=True)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(f"{digest}  {target.name}\n")
    retain = max(1, int(os.environ.get("FUSE_BACKUP_RETAIN_DB", "14")))
    for old in sorted(root.glob("fuse-mariadb-*.sql.gz"), reverse=True)[retain:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
    print(target)
    return target


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else "backups")
