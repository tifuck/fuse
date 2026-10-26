"""Boot sweep after a crash between a skill directory move and its row commit."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.skill_install import content_hash, sweep_partial_swaps  # noqa: E402

OLD = {"SKILL.md": b"---\nname: demo\ndescription: old\n---\nold body\n"}
NEW = {"SKILL.md": b"---\nname: demo\ndescription: new\n---\nnew body\n", "notes.md": b"n"}


def _write(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True)
    for rel, blob in files.items():
        (root / rel).write_bytes(blob)


class SkillSwapRecoveryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.user = self.root / "7"

    def _sweep(self, stored):
        return sweep_partial_swaps(self.root, row_hash=lambda uid, slug: stored if (uid, slug) == (7, "demo") else None)

    def test_install_whose_row_never_committed_gets_the_old_tree_back(self):
        _write(self.user / "demo", NEW)
        _write(self.user / ".old-demo-abc", OLD)
        self._sweep(content_hash(OLD))
        self.assertEqual((self.user / "demo" / "SKILL.md").read_bytes(), OLD["SKILL.md"])
        self.assertFalse((self.user / "demo" / "notes.md").exists())
        self.assertFalse((self.user / ".old-demo-abc").exists())

    def test_committed_install_keeps_the_new_tree(self):
        _write(self.user / "demo", NEW)
        _write(self.user / ".old-demo-abc", OLD)
        self._sweep(content_hash(NEW))
        self.assertEqual((self.user / "demo" / "SKILL.md").read_bytes(), NEW["SKILL.md"])
        self.assertFalse((self.user / ".old-demo-abc").exists())

    def test_delete_whose_row_survived_gets_its_tree_back(self):
        _write(self.user / ".tmp-del-demo-abc", OLD)
        self._sweep(content_hash(OLD))
        self.assertEqual((self.user / "demo" / "SKILL.md").read_bytes(), OLD["SKILL.md"])

    def test_committed_delete_clears_the_trash(self):
        _write(self.user / ".tmp-del-demo-abc", OLD)
        self._sweep(None)
        self.assertFalse((self.user / "demo").exists())
        self.assertFalse((self.user / ".tmp-del-demo-abc").exists())


if __name__ == "__main__":
    unittest.main()
