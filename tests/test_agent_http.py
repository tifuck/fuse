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

from app.agent_http import router, install_ps1, BINARIES, _SAFE_BIN


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


if __name__ == "__main__":
    unittest.main()
