"""Device sign-in start failures surface as errors inside their own dialog; no database or upstream calls."""
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.web.deps import _pool_flash
from app.web.source_routes import chatgpt, grok, kimi


@pytest.fixture(autouse=True)
def clear_flash():
    _pool_flash.clear()
    yield
    _pool_flash.clear()


@pytest.mark.parametrize("module,start,provider,section", [
    (chatgpt, "chatgpt_start", "chatgpt_mod", "chatgpt"),
    (grok, "grok_start", "grok_mod", "grok"),
    (kimi, "kimi_start", "kimi_mod", "kimi"),
])
def test_start_failure_is_an_error_for_its_own_dialog(monkeypatch, module, start, provider, section):
    monkeypatch.setattr(module, "require_user", lambda request: ({"id": 7}, None))
    monkeypatch.setattr(module.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(getattr(module, provider), "start_device", AsyncMock(side_effect=OSError("proxy refused")))
    handler = getattr(module, start)
    response = asyncio.run(handler(None, "global") if section == "kimi" else handler(None))
    assert response.status_code == 303
    kind, message, where = _pool_flash[7]
    assert (kind, where) == ("err", section)
    assert message.startswith("Could not start")


@pytest.mark.parametrize("dialog,var", [("openai-connect-dialog", "chatgpt_flash_msg"), ("grok-connect-dialog", "grok_flash_msg")])
def test_dialog_reopens_with_the_error(dialog, var):
    html = (Path(__file__).resolve().parents[1] / "templates" / "sources.html").read_text()
    tag = re.search(r'<dialog[^>]*id="%s"[^>]*>' % dialog, html).group(0)
    assert f"or {var} %}} data-auto-open" in tag
    body = html[html.index(tag):html.index("</dialog>", html.index(tag))]
    assert f'<p class="callout fault" role="alert">{{{{ {var} }}}}</p>' in body
