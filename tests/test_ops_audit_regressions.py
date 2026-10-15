"""Operational regression checks; never touch services or real databases."""
import gzip
import io
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts import backup_mariadb

ROOT = Path(__file__).resolve().parents[1]


def test_simultaneous_backups_have_distinct_outputs(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(_):
            return datetime(2026, 10, 11, tzinfo=timezone.utc)
    def dump(*args, **kwargs):
        return SimpleNamespace(stdout=io.BytesIO(b"sql fixture"), stderr=io.BytesIO(b""),
                               wait=lambda: 0, poll=lambda: 0)
    monkeypatch.setattr(backup_mariadb, "datetime", Clock)
    monkeypatch.setattr(backup_mariadb.subprocess, "Popen", dump)
    monkeypatch.setattr(backup_mariadb, "settings", SimpleNamespace(database_url="mysql://u:p@localhost/fixture"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: backup_mariadb.backup(tmp_path), range(2)))
    assert len(set(paths)) == 2
    for path in paths:
        assert gzip.decompress(path.read_bytes()) == b"sql fixture"
        assert path.with_suffix(path.suffix + ".sha256").exists()


def test_systemd_unit_runs_start_sh_with_settings_then_secrets():
    # --print-unit exits before installer side effects.
    unit = subprocess.check_output(["bash", str(ROOT / "install.sh"), "--print-unit", "system"], text=True)
    # start.sh owns the uvicorn flags and their ${VAR:-default}s; systemd does
    # not support that expansion, so the unit must not carry any.
    assert f"ExecStart={ROOT}/start.sh\n" in unit
    assert "${" not in unit
    # Optional settings file first, secrets last (later EnvironmentFile wins).
    assert unit.index(f"EnvironmentFile=-{ROOT}/config.env") < unit.index(f"EnvironmentFile={ROOT}/.env")
    start = (ROOT / "start.sh").read_text()
    assert '--port "${PORT:-8088}"' in start and '--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"' in start
