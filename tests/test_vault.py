from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-vault-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "vault.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db, hosts, vault
from app.crypto import decrypt, decrypt_with_dek


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-vault-")
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
        accounts.create_user("1@test.invalid", "password12", admin=False)

    def test_host_secret_not_app_secret_fernet(self):
        vault.unlock(1, "password12")
        host = hosts.add_host(
            1,
            {
                "alias": "box",
                "hostname": "example.com",
                "username": "deploy",
                "auth_type": "password",
                "secret": "s3cret",
            },
        )
        row = db.fetchone("SELECT secret FROM user_hosts WHERE id=?", (host["id"],))
        with self.assertRaises(Exception):
            decrypt(row["secret"])
        dek = vault.get_dek(1)
        self.assertEqual(decrypt_with_dek(row["secret"], dek), "s3cret")
        self.assertNotIn("s3cret", str(host))

    def test_reserved_alias_post_and_patch(self):
        vault.unlock(1, "password12")
        with self.assertRaises(hosts.HostError):
            hosts.add_host(1, {"alias": "local", "hostname": "example.com", "username": "u", "auth_type": "key", "secret": "k"})
        host = hosts.add_host(
            1,
            {"alias": "box", "hostname": "example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        with self.assertRaises(hosts.HostError):
            hosts.patch_host(1, host["id"], {"alias": "LOCAL"})

    def test_lock_logout_and_decrypt_failure_does_not_blank(self):
        vault.unlock(1, "password12")
        host = hosts.add_host(
            1,
            {"alias": "box", "hostname": "example.com", "username": "u", "auth_type": "password", "secret": "s3cret"},
        )
        row = db.fetchone("SELECT secret FROM user_hosts WHERE id=?", (host["id"],))
        cipher = row["secret"]
        vault.lock(1)
        with self.assertRaises(hosts.HostError) as raised:
            hosts.secret_plain(1, host["id"])
        self.assertEqual(raised.exception.status_code, 401)
        again = db.fetchone("SELECT secret FROM user_hosts WHERE id=?", (host["id"],))
        self.assertEqual(again["secret"], cipher)

    def test_password_change_rewraps(self):
        vault.unlock(1, "password12")
        host = hosts.add_host(
            1,
            {"alias": "box", "hostname": "example.com", "username": "u", "auth_type": "password", "secret": "s3cret"},
        )
        accounts.change_password(1, "password12", "password99")
        self.assertEqual(hosts.secret_plain(1, host["id"])[0], "s3cret")
        vault.lock(1)
        with self.assertRaises(Exception):
            vault.unlock(1, "password12")
        vault.unlock(1, "password99")
        self.assertEqual(hosts.secret_plain(1, host["id"])[0], "s3cret")

    def test_catalog_clip(self):
        vault.unlock(1, "password12")
        for i in range(24):
            h = hosts.add_host(
                1,
                {
                    "alias": f"h{i}",
                    "hostname": f"n{i}.example.com",
                    "username": "u",
                    "auth_type": "key",
                    "secret": "k",
                },
            )
            hosts.add_project(1, h["id"], {"name": "proj", "remote_path": "/very/long/path/for/clipping/" + ("x" * 40)})
        text = hosts.catalog_for_prompt(1)
        self.assertLessEqual(len(text), hosts.CATALOG_CHARS)
        # Clipped on whole lines: no path is cut mid-word.
        for line in text.splitlines()[1:-1]:
            self.assertTrue(line.endswith("x" * 40 + ")"), line)
        # The bound host leads even when it is listed last.
        bound = hosts.catalog_for_prompt(1, bound_host_id=h["id"])
        self.assertTrue(bound.splitlines()[2].startswith("- h23 "))
        self.assertLessEqual(len(bound), hosts.CATALOG_CHARS)

    def test_keep_cookie_restores_after_lock(self):
        vault.unlock(1, "password12")
        token = vault.issue_keep(1)
        self.assertTrue(token)
        vault.lock_all()
        self.assertFalse(vault.unlocked(1))
        self.assertTrue(vault.restore_keep(1, token))
        self.assertTrue(vault.unlocked(1))
        host = hosts.add_host(
            1,
            {"alias": "keep", "hostname": "example.com", "username": "u", "auth_type": "password", "secret": "s3cret"},
        )
        self.assertEqual(hosts.secret_plain(1, host["id"])[0], "s3cret")

    def test_keep_cookie_rejects_other_user(self):
        vault.unlock(1, "password12")
        token = vault.issue_keep(1)
        vault.lock_all()
        self.assertFalse(vault.restore_keep(99, token))
        self.assertFalse(vault.unlocked(1))

    def test_settings_and_pairing_validate_types(self):
        with self.assertRaises(hosts.HostError):
            hosts.set_settings(1, agent_coding="false")
        self.assertFalse(hosts.settings_for(1)["agent_coding"])
        with self.assertRaises(hosts.HostError):
            hosts.set_settings(1, allow_nested_spawn=1)
        with self.assertRaises(hosts.HostError):
            hosts.mint_pairing(1, 123)


class SshSsrfTest(unittest.TestCase):
    def test_loopback_blocked(self):
        from app.ssh_pool import SshError, resolve_public

        with self.assertRaises(SshError):
            resolve_public("127.0.0.1", 22)
        with self.assertRaises(SshError):
            resolve_public("localhost", 22)

    def test_private_literal_follows_allow_private_hosts_and_self_is_refused(self):
        from unittest import mock

        from app import ssh_pool

        with mock.patch.object(ssh_pool.settings, "allow_private_hosts", True):
            self.assertEqual(ssh_pool.resolve_public("10.0.0.5", 22), ["10.0.0.5"])
            with mock.patch.object(ssh_pool, "_self_addresses", return_value=frozenset({"10.0.0.5"})):
                with self.assertRaises(ssh_pool.SshError):
                    ssh_pool.resolve_public("10.0.0.5", 22)
        with mock.patch.object(ssh_pool.settings, "allow_private_hosts", False):
            with self.assertRaises(ssh_pool.SshError):
                ssh_pool.resolve_public("10.0.0.5", 22)


class VaultMariaDBRaceTest(unittest.TestCase):
    def setUp(self):
        from tests.mariadb_fixture import isolated_database

        fixture = isolated_database()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        vault.lock_all()
        self.uid = accounts.create_user("race@test.invalid", "password12", admin=False)["id"]
        db.execute("UPDATE users SET vault_salt=NULL, wrapped_dek=NULL, vault_kdf=NULL WHERE id=?", (self.uid,))

    def test_concurrent_first_unlock_of_a_legacy_account_shares_one_dek(self):
        import threading

        real_create, real_ensure = vault.create_wrapped_dek, vault.ensure_user_vault
        first_in, second_in, deks = threading.Event(), threading.Event(), []

        def create(password):
            # Hold the first creator briefly: without the row lock a second
            # unlock also finds no DEK and creates (and stores) its own.
            if not first_in.is_set():
                first_in.set()
                second_in.wait(1.0)
            else:
                second_in.set()
            return real_create(password)

        def ensure(conn, user_id, password):
            deks.append(real_ensure(conn, user_id, password))
            return deks[-1]

        with patch.object(vault, "create_wrapped_dek", create), patch.object(vault, "ensure_user_vault", ensure):
            threads = [threading.Thread(target=vault.unlock, args=(self.uid, "password12")) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(len(deks), 2)
        self.assertEqual(deks[0], deks[1])


if __name__ == "__main__":
    unittest.main()
