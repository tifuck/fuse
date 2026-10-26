from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-agent-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "agent.db"))
os.environ.setdefault("APP_SECRET", "y" * 32)

from app import accounts, db, hosts, vault


class AgentPairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-agent-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        vault.lock_all()
        accounts.create_user("agent@test.invalid", "password12", admin=False)
        vault.lock_all()

    def test_mint_without_vault(self):
        self.assertFalse(vault.unlocked(1))
        # Install lines are built from the public origin; set one so the test
        # does not depend on this machine's PUBLIC_BASE_URL.
        with patch.object(hosts.settings, "public_base_url", "https://fuse.test"):
            pairing = hosts.mint_pairing(1)
        self.assertTrue(pairing["code"].startswith("fuse_"))
        self.assertEqual(pairing["install"], f"curl -fsSL https://fuse.test/i/{pairing['code']} | bash")
        self.assertIn(pairing["code"] + ".ps1", pairing["install_windows"])
        self.assertIn("fuse-agent-windows-amd64.exe", pairing["download_windows"])
        pending = hosts.list_pending_pairings(1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], pairing["id"])
        # Reveal-once: the code lives only in the mint response.
        self.assertEqual(pending[0]["code"], "")
        self.assertEqual(pending[0]["install"], "")
        self.assertTrue(pending[0]["prefix"])
        row = db.fetchone("SELECT code FROM host_pairings WHERE id=?", (pairing["id"],))
        self.assertEqual(row["code"], "")

    def test_redeem_once_creates_agent_host(self):
        pairing = hosts.mint_pairing(1)
        view, token = hosts.redeem_pairing(
            pairing["code"],
            {"hostname": "laptop.local", "username": "ada", "ver": "1.0.0"},
        )
        self.assertEqual(view["auth_type"], "agent")
        self.assertEqual(view["kind"], "agent")
        self.assertTrue(token.startswith("fa_"))
        self.assertEqual(view["username"], "ada")
        self.assertIn("laptop", view["alias"])
        bundle = hosts.list_bundle(1)
        self.assertEqual(bundle["hosts"][0]["auth_type"], "agent")
        self.assertFalse(bundle["pending"])
        with self.assertRaises(hosts.HostError):
            hosts.redeem_pairing(pairing["code"], {"hostname": "other"})
        row = hosts.host_by_device_token(token)
        self.assertIsNotNone(row)
        self.assertEqual(int(row["id"]), view["id"])
        self.assertIsNone(hosts.host_by_device_token("fa_nope"))

    def test_undo_pairing_lets_the_same_code_pair_again(self):
        # hello_ok (the only copy of the device token) could not be sent.
        pairing = hosts.mint_pairing(1)
        view, token = hosts.redeem_pairing(pairing["code"], {"hostname": "laptop.local"})
        hosts.undo_pairing(1, view["id"], pairing["code"])
        self.assertIsNone(hosts.host_by_device_token(token))
        again, _ = hosts.redeem_pairing(pairing["code"], {"hostname": "laptop.local"})
        self.assertEqual(len(hosts.list_bundle(1)["hosts"]), 1)
        self.assertEqual(again["auth_type"], "agent")

    def test_undo_pairing_reopens_the_code_with_the_delete(self):
        # Whatever fails after the host delete commits, the code is reopened by
        # that same commit: it can never be burned with its host gone.
        pairing = hosts.mint_pairing(1)
        view, _token = hosts.redeem_pairing(pairing["code"], {"hostname": "laptop.local"})
        with patch.object(hosts, "_evict_pool", side_effect=RuntimeError("after commit")):
            with self.assertRaises(RuntimeError):
                hosts.undo_pairing(1, view["id"], pairing["code"])
        again, _ = hosts.redeem_pairing(pairing["code"], {"hostname": "laptop.local"})
        self.assertEqual(again["auth_type"], "agent")

    def test_redeem_stores_windows_os(self):
        pairing = hosts.mint_pairing(1)
        view, _token = hosts.redeem_pairing(
            pairing["code"],
            {
                "hostname": "pc",
                "username": "ada",
                "ver": "1.1.2",
                "os": "windows",
                "arch": "windows-amd64",
            },
        )
        self.assertEqual(view["agent_os"], "windows")
        self.assertEqual(view["agent_arch"], "windows-amd64")
        self.assertIn("powershell", hosts.catalog_for_prompt(1, clip=False).lower())

    def test_expired_and_invalid(self):
        with self.assertRaises(hosts.HostError):
            hosts.redeem_pairing("not-a-code", {})
        pairing = hosts.mint_pairing(1)
        db.execute("UPDATE host_pairings SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (pairing["id"],))
        with self.assertRaises(hosts.HostError) as ctx:
            hosts.redeem_pairing(pairing["code"], {"hostname": "x"})
        self.assertIn("expired", str(ctx.exception.detail))

    def test_cap_pending(self):
        for _ in range(hosts.MAX_PENDING_PAIRINGS):
            hosts.mint_pairing(1)
        with self.assertRaises(hosts.HostError):
            hosts.mint_pairing(1)

    def test_secret_plain_rejects_agent(self):
        pairing = hosts.mint_pairing(1)
        view, _token = hosts.redeem_pairing(pairing["code"], {"hostname": "box"})
        with self.assertRaises(hosts.HostError):
            hosts.secret_plain(1, view["id"])

    def test_cancel(self):
        pairing = hosts.mint_pairing(1)
        self.assertTrue(hosts.cancel_pairing(1, pairing["id"]))
        self.assertFalse(hosts.list_pending_pairings(1))
        with self.assertRaises(hosts.HostError):
            hosts.redeem_pairing(pairing["code"], {"hostname": "x"})

    def test_disabled_agent_host_refuses_bound_dispatch(self):
        import asyncio
        import json

        from app import workspace

        pairing = hosts.mint_pairing(1)
        view, _token = hosts.redeem_pairing(pairing["code"], {"hostname": "box", "username": "u"})
        hosts.patch_host(1, view["id"], {"disabled": True})
        text, rec = asyncio.run(
            workspace.execute(
                "run_terminal_command",
                json.dumps({"command": "echo hi"}),
                user_id=1,
                bound_host_id=view["id"],
                bound_cwd="/tmp",
            )
        )
        self.assertEqual(rec["status"], "error")
        self.assertIn("disabled", text)


if __name__ == "__main__":
    unittest.main()
