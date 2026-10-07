from pathlib import Path
from unittest.mock import patch

from scripts.source_sizes import source_sizes


def test_report_excludes_fixtures_vendor_minified_and_symlinks(tmp_path):
    files = {
        "app/example.py": "one\ntwo\n",
        "static/example.js": "one\n",
        "tests/fixtures/page.html": "fixture\n",
        "agent/third_party/lib.c": "vendor\n",
        "static/example.min.js": "minified\n",
        "notes.md": "docs\n",
    }
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (tmp_path / "linked.py").symlink_to(tmp_path / "app/example.py")
    names = [*files, "linked.py", "missing.py", "app/example.py"]
    with patch("scripts.source_sizes.subprocess.check_output", return_value="\0".join(names).encode()):
        assert list(source_sizes(tmp_path)) == [(2, "app/example.py"), (1, "static/example.js")]
