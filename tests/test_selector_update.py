"""Start the source update from the selector and hand the terminal to the update command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture import SkillsetFixture, git
from pilot import PilotTestCase, selector


class UpdateKeyTests(PilotTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillset-selector-update-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        fixture = SkillsetFixture(self.base, {"acme/skills": {"alpha": "alpha"}}, [])
        self.repo = fixture.repo
        git(["branch", "-M", "main"], self.repo)
        environment = patch.dict(os.environ, fixture.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    async def test_update_key_refuses_with_the_reason_and_keeps_the_selector_open(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "ctrl+u")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"),
                             "⎿ Cannot update sources: the selection has unsaved changes; save and install first")
            await pilot.press("space")
            git(["checkout", "-q", "-b", "topic"], self.repo)
            await pilot.press("ctrl+u")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"),
                             f"⎿ Cannot update sources: {selector.update_sources.update_blocker(self.repo)}")

    async def test_update_key_on_a_clean_main_closes_the_selector_with_the_update_handoff(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIn("ctrl+u update", self.text(app, "#hints"))
            await pilot.press("ctrl+u")
            self.assertFalse(app.is_running)
            self.assertIs(app.return_value, selector.update_sources_in_terminal)

    def test_update_handoff_becomes_the_command_on_the_terminal_with_the_shell_path(self) -> None:
        command = self.repo / "scripts/update-sources.sh"
        command.write_text('#!/bin/sh\nread answer\nprintf "%s %s %s\\n" "$$" "$answer" "$PATH"\nexit 7\n',
                           encoding="utf-8")
        shell_path = os.pathsep.join([str(self.base / "bin"), "/usr/bin", "/bin"])
        handoff = ("import sys; from pathlib import Path; from pilot import selector; "
                   "sys.exit(selector.update_sources_in_terminal(Path(sys.argv[1])))")
        process = subprocess.Popen(
            [sys.executable, "-c", handoff, str(self.repo)], text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent),
                     PATH=os.pathsep.join([str(Path(sys.prefix, "bin")), shell_path])),
        )
        stdout, stderr = process.communicate("accept\n", timeout=60)
        self.assertEqual((process.returncode, stdout), (7, f"{process.pid} accept {shell_path}\n"), stderr)


if __name__ == "__main__":
    unittest.main()
