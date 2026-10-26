from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)

import pytest  # noqa: E402

from app import sources  # noqa: E402
from app.sources import llama as llama_mod, ollama as ollama_mod  # noqa: E402

pytestmark = pytest.mark.usefixtures("isolated_db")


def test_admin_server_change_drops_the_llama_and_ollama_lists():
    llama_id = sources.ensure_llama_source()
    ollama_id = sources.ensure_ollama_source()
    sources.db.execute("UPDATE sources SET enabled=1 WHERE id IN (?,?)", (llama_id, ollama_id))
    llama_mod._models_cache[llama_id] = (0.0, ["old-server-model"])
    ollama_mod._tags_cache[ollama_id] = (0.0, ["old-tag"])
    sources.invalidate_model_cache("llama")
    sources.invalidate_model_cache("ollama")
    assert llama_id not in llama_mod._models_cache
    assert ollama_id not in ollama_mod._tags_cache
