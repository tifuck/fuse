"""Files a shell command changed (Claude Code's bashEditDiff): the snapshot
scripts against a real repository, and the trace fields parsed from them."""
import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import workspace


class _Res:
    def __init__(self, stdout: str, exit_status: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.exit_status = exit_status


def _bash(script: str, cwd: str) -> _Res:
    done = subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True)
    return _Res(done.stdout, done.returncode)


class ShellEditsTest(unittest.TestCase):
    def setUp(self):
        workspace._edit_skip.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        Path(self.repo, "f.txt").write_text("a\nb\n")
        subprocess.run(["git", "add", "f.txt"], cwd=self.repo, check=True)
        subprocess.run([*git, "commit", "-qm", "init"], cwd=self.repo, check=True)
        # Already dirty before the command: not part of its edit.
        Path(self.repo, "f.txt").write_text("a\nb\nbefore\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _remote(self):
        async def run_remote(user_id, host_id, command, cwd, seconds, *, rate=True):
            self.assertFalse(rate, "snapshots must not spend the model's exec budget")
            return _bash(command, self.repo)

        return patch.object(workspace, "_run_remote", run_remote)

    def test_reports_only_what_the_command_changed(self):
        with self._remote():
            base = asyncio.run(workspace._edit_baseline(1, 2, self.repo))
            self.assertRegex(base, r"^[0-9a-f]{40}$")
            subprocess.run(["bash", "-c", "sed -i 's/^a$/A/' f.txt && echo new > n.txt"], cwd=self.repo, check=True)
            edits = asyncio.run(workspace._edit_changes(1, 2, self.repo, base))
        self.assertEqual(sorted(edits["edited_files"]), ["f.txt", "n.txt"])
        self.assertEqual((edits["lines_added"], edits["lines_removed"]), (2, 1))
        self.assertIn("-a\n+A", edits["edit_diff"])
        self.assertNotIn("+before", edits["edit_diff"])
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertEqual(staged, "", "the repository's own index is never touched")

    def test_no_change_and_non_repositories_report_nothing(self):
        with self._remote():
            base = asyncio.run(workspace._edit_baseline(1, 2, self.repo))
            self.assertEqual(asyncio.run(workspace._edit_changes(1, 2, self.repo, base)), {})
        with tempfile.TemporaryDirectory() as plain:
            async def run_remote(user_id, host_id, command, cwd, seconds, *, rate=True):
                return _bash(command, plain)

            with patch.object(workspace, "_run_remote", run_remote):
                self.assertEqual(asyncio.run(workspace._edit_baseline(1, 2, plain)), "")
                # Not a repository: skipped for a while instead of re-probed on every command.
                calls = []

                async def counting(*args, **kwargs):
                    calls.append(args)
                    return _Res("", 3)

            with patch.object(workspace, "_run_remote", counting):
                self.assertEqual(asyncio.run(workspace._edit_baseline(1, 2, plain)), "")
            self.assertEqual(calls, [])

    def test_rejects_a_malformed_base(self):
        self.assertEqual(asyncio.run(workspace._edit_changes(1, 2, self.repo, "HEAD; rm -rf /")), {})



class ShellAskDescriptionTest(unittest.TestCase):
    """Claude Code's "Bash command" dialog shows the command's description
    above it; the ask carries it to the card and to reconnect seeding."""

    def test_shell_ask_carries_the_description(self):
        from app import permissions

        seen = {}

        async def ask(gate, payload, auto_judge=None):
            seen.update(payload)
            return "deny"

        gate = permissions.Gate(user_id=1, thread_id=7, run_id="r", requester=None, unattended=False,
                                notify=lambda event: None, cancel=None)
        with patch.object(permissions, "bypass_enabled", return_value=False), \
                patch.object(permissions, "auto_enabled", return_value=False), \
                patch.object(permissions, "grant_covers", return_value=False), \
                patch.object(permissions, "_ask_and_wait", ask):
            result = asyncio.run(permissions.review(
                tool="run_terminal_command",
                args={"command": "npm install --prefix /srv/shared", "description": "  Install package\n dependencies "},
                user_id=1, host_id=2, host_alias="box", root="/srv/app", windows=False, gate=gate,
            ))
        self.assertFalse(result.allowed)
        self.assertEqual(seen["description"], "Install package dependencies")
        self.assertEqual(seen["command"], "npm install --prefix /srv/shared")

    def test_reconnect_payload_keeps_a_live_asks_description(self):
        from app import permissions

        permissions._ASK_DESCRIPTIONS["pa_test"] = "Install package dependencies"
        try:
            row = {"id": "pa_test", "status": "pending", "kind": "shell", "tool": "run_terminal_command", "host": "box",
                   "path": "", "cwd": "/srv/app", "command": "npm install", "secret": 0, "requester": "", "expires_at": "",
                   "decision": "", "grant_kind": "", "grant_value": ""}
            self.assertEqual(permissions._public_payload(row)["description"], "Install package dependencies")
            self.assertEqual(permissions._public_payload({**row, "id": "pa_other"})["description"], "")
        finally:
            permissions._ASK_DESCRIPTIONS.pop("pa_test", None)


if __name__ == "__main__":
    unittest.main()
