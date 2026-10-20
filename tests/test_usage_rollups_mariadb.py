"""Usage rollups stay equal to SUM(usage_events) in a disposable MariaDB schema."""
import os

import pytest

from app import accounts, db
from app.config import settings
from app.mysql_compat import connect
from scripts import rebuild_usage_rollups as rebuild
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")

USAGE = {"prompt_tokens": 1000, "completion_tokens": 400, "usage_status": "complete"}


def _seed():
    ts = db.now()
    for uid in (1, 2):
        db.execute("INSERT INTO users(id,email,password_hash,is_admin,created_at) VALUES(?,?,'unused',?,?)",
                   (uid, f"rollup{uid}@test.invalid", 1 if uid == 1 else 0, ts))
    db.execute("INSERT INTO api_keys(id,user_id,name,prefix,key_hash,created_at) VALUES(7,2,'Laptop','sk-fuse-ab','h',?)", (ts,))
    with db.tx() as conn:
        conn.execute("INSERT INTO sources (type, name, enabled, config, created_at) VALUES ('zai','Z.AI',1,'{}',?)", (ts,))
        source_id = db.last_id(conn)
    cred = accounts.add_credential(source_id, "pool", "secret", donated_by=1)
    for uid, key in ((1, None), (2, 7), (2, 7)):
        db.record_usage(uid, source_id, cred, "glm-x", dict(USAGE), key_id=key, provider="zai")
    return source_id, cred


def _report():
    conn = connect(settings.database_url, max_connections=1)
    try:
        return rebuild.report(conn)
    finally:
        conn.close()


def _clean():
    return dict.fromkeys(rebuild.REPORT, 0)


def test_reprice_up_and_down_moves_every_rollup_with_its_events():
    with isolated_database():
        _seed()
        db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
        assert _report() == _clean()
        assert db.fetchone("SELECT SUM(cost_micros) AS c FROM usage_hourly")["c"] == 3 * (1000 + 800)
        # Below what the rollups hold: no floor at zero may swallow the delta.
        db.execute("UPDATE credentials SET cost_micros=0")
        db.save_model_price("zai", "glm-x", 0, None, 0, None)
        assert int(db.fetchone("SELECT cost_micros FROM credentials")["cost_micros"]) == -3 * 1800
        db.execute("UPDATE credentials SET cost_micros=0")
        db.delete_model_price("zai", "glm-x")
        assert _report() == _clean()


def test_reprice_pages_history_by_id_window(monkeypatch):
    # One id per window, another model's event inside the span: every window
    # must still move the rollups by exactly what its events moved.
    monkeypatch.setattr(db, "REPRICE_WINDOW", 1)
    with isolated_database():
        source_id, cred = _seed()
        db.record_usage(1, source_id, cred, "glm-y", dict(USAGE), provider="zai")
        db.record_usage(1, source_id, cred, "glm-x", dict(USAGE), provider="zai")
        db.save_model_price("zai", "glm-x", 1_000_000, None, 2_000_000, None)
        assert _report() == _clean()
        assert db.fetchone("SELECT SUM(cost_micros) AS c FROM usage_hourly WHERE model='glm-x'")["c"] == 4 * 1800
        assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE pricing_known=1")["n"] == 4
        db.delete_model_price("zai", "glm-x")
        assert _report() == _clean()
        assert db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE pricing_known=1")["n"] == 0


def test_delete_user_takes_their_events_out_of_credential_rollups():
    with isolated_database():
        _seed()
        accounts.delete_user(2, actor_id=1)
        assert _report() == _clean()
        assert int(db.fetchone("SELECT requests FROM credentials")["requests"]) == 1


def test_apply_rebuilds_drifted_rollups():
    with isolated_database():
        _seed()
        db.execute("UPDATE credentials SET cost_micros=cost_micros+5, requests=0")
        db.execute("UPDATE provider_usage SET total_tokens=1")
        db.execute("UPDATE usage_hourly SET requests=99")
        db.execute("DELETE FROM provider_usage WHERE user_id=1")
        db.execute("DELETE FROM usage_hourly WHERE user_id=1")
        drift = _report()
        assert drift["credentials"] and drift["provider_usage"] and drift["provider_usage missing"] and drift["usage_hourly"]
        assert drift["usage_hourly missing"] == 1
        conn = connect(settings.database_url, max_connections=1)
        try:
            rebuild.apply(conn)
            conn.commit()
        finally:
            conn.close()
        assert _report() == _clean()
