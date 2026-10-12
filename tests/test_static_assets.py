"""Content-versioned /static: rewritten imports, transitive versions, CSS inlining."""
import asyncio
import re
from pathlib import Path

import pytest

from app import static_assets

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "static"
    root.mkdir()
    monkeypatch.setattr(static_assets, "ROOT", root)
    monkeypatch.setattr(static_assets, "_ROOT_REAL", root.resolve())
    monkeypatch.setattr(static_assets, "STAT_TTL", 0)
    for cache in (static_assets._files, static_assets._versions, static_assets._bodies):
        cache.clear()

    def write(rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    yield write
    for cache in (static_assets._files, static_assets._versions, static_assets._bodies):
        cache.clear()


def test_imports_point_at_versioned_urls(tree):
    tree("main.js", "import { a } from './lib/a.js';\nimport './side.js';\nconst b = await import(\"./lib/b.js\");\n"
                    "export { c } from '../outside.js';\nimport x from 'bare';\nconst s = 'from ./lib/a.js';\n")
    tree("lib/a.js", "export const a = 1;\n")
    tree("lib/b.js", "import { a } from './a.js';\nexport const b = a;\n")
    tree("side.js", "")
    _, body = static_assets.rewritten("main.js")
    text = body.decode()
    va, vb, vs = (static_assets.version(rel) for rel in ("lib/a.js", "lib/b.js", "side.js"))
    assert f"from './lib/a.js?v={va}'" in text
    assert f"import './side.js?v={vs}'" in text
    assert f'import("./lib/b.js?v={vb}")' in text
    # Outside static/, bare specifiers and plain strings are untouched.
    assert "from '../outside.js'" in text and "from 'bare'" in text and "'from ./lib/a.js'" in text
    assert static_assets.preloads("main.js") == [f"/static/lib/a.js?v={va}", f"/static/lib/b.js?v={vb}", f"/static/side.js?v={vs}"]


def test_a_leaf_change_reversions_every_importer(tree):
    tree("main.js", "import './mid.js';\n")
    tree("mid.js", "import './leaf.js';\n")
    tree("leaf.js", "export default 1;\n")
    tree("other.js", "export default 2;\n")
    before = {rel: static_assets.version(rel) for rel in ("main.js", "mid.js", "leaf.js", "other.js")}
    tree("leaf.js", "export default 3;\n")
    after = {rel: static_assets.version(rel) for rel in before}
    assert all(after[rel] != before[rel] for rel in ("main.js", "mid.js", "leaf.js"))
    assert after["other.js"] == before["other.js"]
    assert f"leaf.js?v={after['leaf.js']}" in static_assets.rewritten("mid.js")[1].decode()


def test_import_cycles_have_a_version(tree):
    tree("a.js", "import './b.js';\n")
    tree("b.js", "import './a.js';\n")
    assert static_assets.version("a.js") and static_assets.version("b.js")
    assert "./a.js?v=" in static_assets.rewritten("b.js")[1].decode()


def test_css_imports_are_inlined_in_order(tree):
    tree("app.css", '/* entry */\n@import url("styles/one.css");\n@import "styles/two.css";\nbody { color: red; }\n')
    tree("mixed.css", '@import url("styles/one.css");\n@import url("styles/print.css") print;\n')
    tree("styles/one.css", ".one { background: url(../img/dot.png); }\n")
    tree("styles/two.css", ".two { background: url('data:image/png;base64,AA=='); }\n")
    tree("styles/print.css", ".print {}\n")
    tree("img/dot.png", "png")
    text = static_assets.rewritten("app.css")[1].decode()
    assert text.index(".one") < text.index(".two") < text.index("body {")
    # Inlined url() refs become absolute so they still resolve from app.css.
    assert f"url(/static/img/dot.png?v={static_assets.version('img/dot.png')})" in text
    assert "url('data:image/png;base64,AA==')" in text
    assert "@import" not in text
    # One import with a media list keeps every import of that sheet an
    # @import (versioned), so none is stranded after inlined rules.
    mixed = static_assets.rewritten("mixed.css")[1].decode()
    assert f'@import url("/static/styles/one.css?v={static_assets.version("styles/one.css")}");' in mixed
    assert f'@import url("/static/styles/print.css?v={static_assets.version("styles/print.css")}") print;' in mixed


def test_paths_outside_static_are_refused(tree, tmp_path):
    (tmp_path / "secret.js").write_text("secret")
    assert static_assets.rewritten("../secret.js") is None
    assert static_assets.version("../secret.js") is None
    assert static_assets.asset_url("missing.js") == "/static/missing.js"


def test_served_scripts_answer_conditional_requests(tree, monkeypatch):
    from app.config import settings
    from app.main import VersionedStaticFiles

    tree("main.js", "import './dep.js';\n")
    tree("dep.js", "")
    files = VersionedStaticFiles(directory=str(static_assets.ROOT))
    current = static_assets.version("main.js")
    monkeypatch.setattr(settings, "static_immutable", True)

    def get(etag=None):
        headers = [(b"if-none-match", etag.encode())] if etag else []
        scope = {"type": "http", "method": "GET", "path": "/main.js", "query_string": f"v={current}".encode(), "headers": headers}
        return asyncio.run(files.get_response("main.js", scope))

    first = get()
    assert first.status_code == 200 and first.headers["etag"] == f'"{current}"'
    assert f"./dep.js?v={static_assets.version('dep.js')}" in first.body.decode()
    assert get(f'"{current}"').status_code == 304


def test_templates_use_content_versions():
    # Hand-numbered ?v= on a template entry would be served immutable while
    # the modules it imports moved on; asset() derives it from content.
    for path in (ROOT / "templates").rglob("*.html"):
        assert not re.search(r"/static/[^\"'?\s]+\?v=\d+", path.read_text()), path
