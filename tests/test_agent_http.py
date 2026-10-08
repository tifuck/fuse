from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-http-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "http.db"))
os.environ.setdefault("APP_SECRET", "z" * 32)

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_http import router, install_script, install_ps1, BINARIES, _SAFE_BIN, _sha256


class AgentHttpTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_safe_bin_accepts_windows_exe(self):
        self.assertTrue(_SAFE_BIN.match("fuse-agent-windows-amd64.exe"))
        self.assertTrue(_SAFE_BIN.match("fuse-agent-windows-amd64.exe.sha256"))
        self.assertTrue(_SAFE_BIN.match("fuse-agent-linux-amd64"))
        self.assertFalse(_SAFE_BIN.match("fuse-agent-windows-amd64"))
        self.assertFalse(_SAFE_BIN.match("../fuse-agent-linux-amd64"))
        self.assertIn("windows-amd64", BINARIES)
        self.assertIn("windows-arm64", BINARIES)

    def test_ps1_bakes_code_and_origin(self):
        with patch("app.agent_http._origin", return_value="https://example.test"):
            body = install_ps1("fuse_abcDEF12-_xx")
        self.assertIn("https://example.test", body)
        self.assertIn("fuse_abcDEF12-_xx", body)
        self.assertIn("windows-amd64", body)
        self.assertIn("checksum", body.lower())
        self.assertIn("Get-FileHash", body)
        self.assertIn("install --url", body)

    def test_i_ps1_route(self):
        with patch("app.agent_http._origin", return_value="https://example.test"), patch(
            "app.agent_http.limiter.allow", return_value=True
        ):
            res = self.client.get("/i/fuse_abcDEF12.ps1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("fuse_abcDEF12", res.text)
        self.assertIn("Invoke-WebRequest", res.text)

    def test_i_ps1_rejects_bad_code(self):
        with patch("app.agent_http._origin", return_value="https://example.test"), patch(
            "app.agent_http.limiter.allow", return_value=True
        ):
            res = self.client.get("/i/not-a-code.ps1")
        self.assertEqual(res.status_code, 404)

    def test_trailing_newline_code_not_baked(self):
        # `$` alone would accept "fuse_abcdefgh\n" and bake a broken default.
        with patch("app.agent_http._origin", return_value="https://example.test"):
            sh = install_script("fuse_abcdefgh\n")
            ps = install_ps1("fuse_abcdefgh\n")
        self.assertNotIn("fuse_abcdefgh", sh)
        self.assertNotIn("fuse_abcdefgh", ps)
        with patch("app.agent_http._origin", return_value="https://example.test"), patch(
            "app.agent_http.limiter.allow", return_value=True
        ):
            res = self.client.get("/i/fuse_abcdefgh%0a")
        self.assertEqual(res.status_code, 404)

    def test_sha256_cached_until_file_changes(self):
        import hashlib
        import time

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bin"
            p.write_bytes(b"payload-one")
            with patch("app.agent_http.hashlib.sha256", wraps=hashlib.sha256) as spy:
                a = _sha256(p)
                b = _sha256(p)
                self.assertEqual(a, b)
                self.assertEqual(spy.call_count, 1)
                p.write_bytes(b"payload-two-longs")
                os.utime(p, (time.time() + 5, time.time() + 5))
                c = _sha256(p)
                self.assertEqual(spy.call_count, 2)
                self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
