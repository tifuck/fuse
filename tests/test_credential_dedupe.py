from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("APP_SECRET", "x" * 32)

from app import db  # noqa: E402
from app.sources import chatgpt as chatgpt_mod  # noqa: E402
from app.sources import devin as devin_mod  # noqa: E402
from app.sources import kimi as kimi_mod  # noqa: E402
from app.sources import zai as zai_mod  # noqa: E402

import pytest

pytestmark = pytest.mark.usefixtures("isolated_db")


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def _chatgpt_tokens(account: str, email: str, access: str) -> dict:
    claims = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
        "https://api.openai.com/profile": {"email": email},
    }
    return {"access_token": access, "refresh_token": "rt-" + access, "id_token": _jwt(claims), "expires_in": 3600}


def _rows(source_id: int):
    return db.fetchall("SELECT id, disabled FROM credentials WHERE source_id=? ORDER BY id", (source_id,))


class CredentialDedupeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.execute("DELETE FROM credentials")

    def test_chatgpt_repeat_sign_in_reuses_the_row(self):
        sid = chatgpt_mod.ensure_chatgpt_source()
        first = chatgpt_mod.store_tokens(sid, _chatgpt_tokens("acct", "a@example.com", "t1"), donated_by=1)
        db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (first,))
        again = chatgpt_mod.store_tokens(sid, _chatgpt_tokens("acct", "a@example.com", "t2"), donated_by=1)
        self.assertEqual(again, first)
        rows = _rows(sid)
        self.assertEqual([(r["id"], r["disabled"]) for r in rows], [(first, 0)])

    def test_chatgpt_team_seats_stay_separate(self):
        sid = chatgpt_mod.ensure_chatgpt_source()
        first = chatgpt_mod.store_tokens(sid, _chatgpt_tokens("team", "a@example.com", "t1"), donated_by=1)
        second = chatgpt_mod.store_tokens(sid, _chatgpt_tokens("team", "b@example.com", "t2"), donated_by=1)
        self.assertNotEqual(first, second)
        self.assertEqual(len(_rows(sid)), 2)

    async def test_devin_new_session_token_updates_the_account_row(self):
        sid = devin_mod.ensure_devin_source()
        with patch.object(devin_mod, "_store_billing"):
            for key in ("devin-key-1", "devin-key-2"):
                info = {"api_key": key, "email": "d@example.com", "name": "D"}
                with patch.object(devin_mod, "probe_token", AsyncMock(return_value=(info, ""))):
                    cred_id, err = await devin_mod.add_session_token(sid, "raw", 1)
                self.assertEqual(err, "")
        rows = _rows(sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(devin_mod.accounts.cred_secret(db.fetchone("SELECT * FROM credentials WHERE id=?", (rows[0]["id"],))),
                         "devin-key-2")

    async def test_plan_key_added_during_probe_is_not_duplicated(self):
        for mod, ensure in ((kimi_mod, kimi_mod.ensure_kimi_source), (zai_mod, zai_mod.ensure_zai_source)):
            sid = ensure()
            key = "plan-key-12345"

            async def probe(raw, mod=mod, sid=sid):
                mod.store_key(sid, raw, 1)
                return {}, "global", ""

            with patch.object(mod, "probe_key", probe), patch.object(mod, "refresh_quota", AsyncMock()):
                cred_id, err = await mod.add_plan_key(sid, key, 1)
            self.assertIsNone(cred_id)
            self.assertIn("already in the pool", err)
            self.assertEqual(len(_rows(sid)), 1)


if __name__ == "__main__":
    unittest.main()
