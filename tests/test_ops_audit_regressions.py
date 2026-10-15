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


def test_systemd_unit_uses_supported_environment_expansion():
    # --print-unit exits before installer side effects.
    unit = subprocess.check_output(["bash", str(ROOT / "install.sh"), "--print-unit", "system"], text=True)
    assert '${PORT}' in unit and '${FORWARDED_ALLOW_IPS}' in unit
    assert 'Environment="PORT=8088" "FORWARDED_ALLOW_IPS=127.0.0.1"' in unit
    assert '${PORT:-' not in unit and '${FORWARDED_ALLOW_IPS:-' not in unit
