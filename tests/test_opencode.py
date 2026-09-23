"""OpenCode catalog ids, Devin prefixes, and config merge."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-opencode-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "opencode.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import opencode as opencode_mod
from app.config import settings
from app.web import router as web_router


class DisplayPrefixTest(unittest.TestCase):
    def test_devin_names_are_prefixed(self):
        self.assertEqual(opencode_mod.opencode_display_name("devin", "GPT 5.6 Luna"), "Devin GPT 5.6 Luna")
        self.assertEqual(opencode_mod.opencode_display_name("devin", "Devin GPT 5.4"), "Devin GPT 5.4")
        self.assertEqual(opencode_mod.opencode_display_name("chatgpt", "GPT 5.6 Luna"), "GPT 5.6 Luna")
        self.assertEqual(opencode_mod.opencode_display_name("grok", "Grok 4.6"), "Grok 4.6")

    def test_devin_ids_keep_provider_prefix(self):
        from app.sources import devin as devin_mod

        mid = devin_mod.canonical_fuse_id("devin/gpt-5-6-luna")
        self.assertTrue(mid.startswith("devin/"))
        self.assertEqual(mid, "devin/gpt-5-6-luna")


class MergeConfigTest(unittest.TestCase):
    def test_replaces_fuse_models_and_keeps_other_providers(self):
        existing = {
            "$schema": "https://opencode.ai/config.json",
            "model": "other/x",
            "provider": {
                "openai": {"npm": "@ai-sdk/openai", "models": {"gpt-4": {}}},
                "fuse": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Fuse",
                    "options": {"baseURL": "https://old.example/v1", "apiKey": "sk-old"},
                    "models": {"stale": {"name": "Stale"}},
                },
            },
            "plugin": ["keep-me"],
        }
        out = opencode_mod.merge_opencode_config(
            existing,
            models={"devin/gpt-5-6-luna": {"name": "Devin GPT 5.6 Luna"}},
            base_url="https://inference.example/v1",
            api_key="sk-new",
        )
        self.assertEqual(out["model"], "other/x")
        self.assertEqual(out["plugin"], ["keep-me"])
        self.assertIn("openai", out["provider"])
        self.assertEqual(out["provider"]["openai"]["models"]["gpt-4"], {})
        fuse = out["provider"]["fuse"]
        self.assertEqual(fuse["options"]["baseURL"], "https://inference.example/v1")
        self.assertEqual(fuse["options"]["apiKey"], "sk-new")
        self.assertEqual(list(fuse["models"]), ["devin/gpt-5-6-luna"])
        self.assertNotIn("stale", fuse["models"])
        self.assertTrue(list(fuse["models"])[0].startswith("devin/"))

    def test_empty_api_key_does_not_replace_saved_key(self):
        existing = {
            "provider": {
                "fuse": {
                    "options": {"baseURL": "https://old.example/v1", "apiKey": "sk-keep"},
                    "models": {"stale": {"name": "Stale"}},
                }
            }
        }
        out = opencode_mod.merge_opencode_config(
            existing,
            models={"grok-4.6": {"name": "Grok 4.6"}},
            base_url="https://inference.example/v1",
            api_key="",
        )
        self.assertEqual(out["provider"]["fuse"]["options"]["apiKey"], "sk-keep")
        self.assertEqual(list(out["provider"]["fuse"]["models"]), ["grok-4.6"])

    def test_reads_saved_key_from_json_and_jsonc(self):
        cfg = {"provider": {"fuse": {"options": {"apiKey": "sk-from-json"}}}}
        self.assertEqual(opencode_mod.existing_fuse_api_key(cfg), "sk-from-json")
        raw = '{"provider":{"fuse":{"options":{"apiKey":"sk-from-jsonc"}}}}'
        parsed = opencode_mod.parse_opencode_text("// header\n" + raw)
        self.assertEqual(opencode_mod.existing_fuse_api_key(parsed), "sk-from-jsonc")
        self.assertEqual(opencode_mod.existing_fuse_api_key({}), "")

    def test_enables_fuse_if_disabled(self):
        out = opencode_mod.merge_opencode_config(
            {"enabled_providers": ["openai"], "disabled_providers": ["fuse", "x"]},
            models={"grok-4.6": {"name": "Grok 4.6"}},
        )
        self.assertIn("fuse", out["enabled_providers"])
        self.assertEqual(out["disabled_providers"], ["x"])

    def test_strip_jsonc_keeps_strings(self):
        raw = '{\n  // comment\n  "a": "https://x.com", /* keep */\n  "b": 1\n}'
        data = json.loads(opencode_mod.strip_jsonc(raw))
        self.assertEqual(data["a"], "https://x.com")
        self.assertEqual(data["b"], 1)


class InstallScriptTest(unittest.TestCase):
    def test_script_is_posix_and_embeds_origin(self):
        script = opencode_mod.install_script(
            origin="https://inference.example",
            api_base="https://inference.example/v1",
        )
        self.assertTrue(script.startswith("#!/bin/sh"))
        self.assertIn("https://inference.example", script)
        self.assertIn("https://inference.example/v1", script)
        self.assertIn("provider.fuse", script)
        self.assertIn("already saved in OpenCode config", script)
        self.assertIn("python3", script)
        compile(script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0], "<install>", "exec")

    def test_install_route_is_public(self):
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web_router)
        client = TestClient(app, base_url="https://testserver")
        resp = client.get("/install-opencode.sh")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("#!/bin/sh", resp.text)
        self.assertIn("python3", resp.text)


if __name__ == "__main__":
    unittest.main()
