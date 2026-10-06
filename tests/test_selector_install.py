"""Drive save-and-install through the dialog and its real terminal handoff."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture import SkillsetFixture, configure_git, git, write_selection
from pilot import PilotTestCase, selector


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillset-selector-install-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = SkillsetFixture(self.base, {
            "mattpocock/skills": {
                "skills/prototype": "prototype", "skills/tdd": "tdd",
            },
        }, ["mattpocock/skills:prototype"])
        self.repo = self.fixture.repo
        self.home = self.fixture.home
        environment = patch.dict(os.environ, self.fixture.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def handoff(self) -> tuple[int, str]:
        # The terminal handoff inherits stdout/stderr, including from real Git
        # and reconciler children. Redirect file descriptors, not Python alone.
        sys.stdout.flush()
        sys.stderr.flush()
        original_stdout, original_stderr = os.dup(1), os.dup(2)
        with tempfile.TemporaryFile() as captured:
            try:
                os.dup2(captured.fileno(), 1)
                os.dup2(captured.fileno(), 2)
                code = selector.install_saved_selection(self.repo)
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(original_stdout, 1)
                os.dup2(original_stderr, 2)
                os.close(original_stdout)
                os.close(original_stderr)
            captured.seek(0)
            return code, captured.read().decode("utf-8")


class InstallHandoffTests(InstallCase):
    """The handoff after the selector exits with a saved selection."""

    def save_tdd_instead_of_prototype(self) -> None:
        selector.skill_catalog.write_selection(self.repo, ["mattpocock/skills:tdd"])

    def test_install_failure_keeps_commit_and_unmanaged_directory(self) -> None:
        unmanaged = self.home / ".claude/skills/tdd"
        unmanaged.mkdir(parents=True)
        (unmanaged / "notes.txt").write_text("Keep my local work.\n", encoding="utf-8")
        previous = git(["rev-parse", "HEAD"], self.repo)
        self.save_tdd_instead_of_prototype()

        code, output = self.handoff()

        self.assertEqual(code, 1, output)
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")
        self.assertIn("unowned or changed install directory entry collision", output)
        self.assertIn(str(unmanaged), output)
        self.assertFalse(unmanaged.is_symlink())
        self.assertEqual((unmanaged / "notes.txt").read_text(), "Keep my local work.\n")
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual(output.splitlines()[-1], "run the skill selector again and choose Install")

    def test_comment_only_change_offers_install_and_commits_saved_bytes(self) -> None:
        head = git(["rev-parse", "HEAD"], self.repo)
        selection = self.repo / "skills.txt"
        selection.write_text(selection.read_text() + "# Kept comment\n", encoding="utf-8")
        before = selection.read_bytes()
        # The selector offers Install whenever the saved bytes differ from HEAD.
        self.assertTrue(selector.skill_catalog.selection_uncommitted(self.repo))

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), head)
        self.assertEqual(git(["show", "HEAD:skills.txt"], self.repo) + "\n", before.decode())
        self.assertEqual(selection.read_bytes(), before)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    def test_rejected_commit_keeps_saved_selection_and_does_not_install(self) -> None:
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho 'selection commit rejected' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        previous = git(["rev-parse", "HEAD"], self.repo)
        self.save_tdd_instead_of_prototype()

        code, output = self.handoff()

        self.assertEqual(code, 1, output)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), previous)
        self.assertEqual((self.repo / "skills.txt").read_text().splitlines()[1:],
                         ["mattpocock/skills:tdd"])
        self.assertIn("M skills.txt", git(["status", "--porcelain"], self.repo))
        self.assertIn("selection commit rejected", output)
        self.assertIn("skills.txt stays saved; nothing was committed or installed", output)
        self.assertNotIn("tracked skillset changes are not committed", output)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_commit_hook_sees_user_pathspec_semantics(self) -> None:
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\ngit diff --cached --name-only -- '*.txt' | grep -qx skills.txt\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        previous = git(["rev-parse", "HEAD"], self.repo)
        self.save_tdd_instead_of_prototype()

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    def test_ahead_branch_ends_with_push_hint_and_leaves_upstream_unchanged(self) -> None:
        remote = self.base / "backup.git"
        git(["init", "--bare", "-q", str(remote)], self.base)
        git(["remote", "add", "origin", str(remote)], self.repo)
        git(["push", "-qu", "origin", "HEAD"], self.repo)
        branch = git(["branch", "--show-current"], self.repo)
        upstream = git(["rev-parse", f"refs/heads/{branch}"], remote)
        self.save_tdd_instead_of_prototype()

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(output.splitlines()[-1], "Not pushed yet. To back it up: git push")
        self.assertEqual(git(["rev-parse", f"refs/heads/{branch}"], remote), upstream)
        self.assertEqual(git(["rev-list", "--count", "@{upstream}..HEAD"], self.repo), "1")


class SelectorInstallTests(InstallCase, PilotTestCase):
    async def assert_save_only(self, reason: str) -> None:
        before = (self.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("right", "space", "ctrl+s")
            self.assertIn(reason, self.text(app, "#dialog-details"))
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            await pilot.press("1")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertEqual((self.repo / "skills.txt").read_bytes(), before)
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            await pilot.press("enter")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
            self.assertIsNone(app.return_value)
        self.assertEqual((self.repo / "skills.txt").read_text().splitlines()[1:], [])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_default_choice_commits_only_selection_then_installs_without_upstream_hint(self) -> None:
        other = self.base / "other-checkout"
        other.mkdir()
        git(["init", "-q"], other)
        configure_git(other)
        write_selection(other, ["other/source:original"])
        git(["add", "skills.txt"], other)
        git(["commit", "-qm", "initial unrelated selection"], other)
        other_head = git(["rev-parse", "HEAD"], other)
        write_selection(other, ["other/source:edited"])
        other_selection = (other / "skills.txt").read_bytes()
        previous = git(["rev-parse", "HEAD"], self.repo)
        with patch.dict(os.environ, {
            "GIT_DIR": str(other / ".git"),
            "GIT_WORK_TREE": str(other),
            "GIT_COMMON_DIR": str(other / ".git"),
            "GIT_INDEX_FILE": str(other / ".git/index"),
        }):
            app = selector.SelectorApp(root=self.repo)
            async with app.run_test() as pilot:
                await app.workers.wait_for_complete()
                await pilot.press("right", "space", "down", "space", "ctrl+s")
                self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
                self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 1)
                self.assertEqual(self.lines(app, "choices"),
                                 ["1. Save and install", "2. Save only", "3. Keep editing"])
                await pilot.press("enter")
                self.assertFalse(app.is_running)
                self.assertIs(app.return_value, True)
            self.assertEqual(git(["rev-parse", "HEAD"], self.repo), previous)
            self.assertEqual(list(self.home.iterdir()), [])

            code, output = self.handoff()

        self.assertEqual(git(["rev-parse", "HEAD"], other), other_head, output)
        self.assertEqual((other / "skills.txt").read_bytes(), other_selection)
        self.assertEqual(git(["status", "--porcelain"], other), "M skills.txt")
        self.assertEqual(code, 0, output)
        self.assertEqual(git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], self.repo),
                         "skills.txt")
        self.assertEqual(git(["log", "-1", "--format=%B"], self.repo),
                         "Update skill selection (+1 -1)\n\n"
                         "+ mattpocock/skills:tdd\n- mattpocock/skills:prototype")
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")
        for directory in (".agents/skills", ".claude/skills"):
            link = self.home / directory / "tdd"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), self.fixture.sources["mattpocock/skills"] / "skills/tdd")
            self.assertFalse((self.home / directory / "prototype").exists())
        self.assertIn("created 2 skill links", output)
        self.assertNotIn("git push", output)

    async def test_committed_never_installed_selection_offers_install_without_a_new_commit(self) -> None:
        before = (self.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            self.assertIn("0 unsaved · not installed", self.text(app, "#title"))
            self.assertEqual(list(self.home.iterdir()), [])
            await pilot.press("ctrl+s")
            self.assertEqual(self.lines(app, "choices"), ["1. Install", "2. Save only", "3. Keep editing"])
            await pilot.press("1")
            self.assertIs(app.return_value, True)
        self.assertEqual((self.repo / "skills.txt").read_bytes(), before)

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual((self.repo / "skills.txt").read_bytes(), before)
        for directory in (".agents/skills", ".claude/skills"):
            self.assertTrue((self.home / directory / "prototype").is_symlink())

    async def test_save_then_restore_head_refreshes_actual_installation_status(self) -> None:
        selector.skill_catalog.write_selection(self.repo, ["mattpocock/skills:prototype"])
        git(["commit", "-qam", "normalize selection"], self.repo)
        before = (self.repo / "skills.txt").read_bytes()
        for installed in (False, True):
            with self.subTest(installed=installed):
                if installed:
                    code, output = self.handoff()
                    self.assertEqual(code, 0, output)
                app = selector.SelectorApp(root=self.repo)
                async with app.run_test() as pilot:
                    await app.workers.wait_for_complete()
                    await pilot.press("right", "down", "space", "ctrl+s", "2")
                    self.assertIn("0 unsaved · not installed", self.text(app, "#title"))
                    await pilot.press("space", "ctrl+s", "2")
                    await app.workers.wait_for_complete()
                    self.assertEqual((self.repo / "skills.txt").read_bytes(), before)
                    self.assertEqual(git(["status", "--porcelain"], self.repo), "")
                    self.assertIn("0 unsaved", self.text(app, "#title"))
                    self.assertEqual("not installed" in self.text(app, "#title"), not installed)
                    await pilot.press("ctrl+s")
                    if installed:
                        self.assertFalse(app.screen.is_modal)
                        self.assertIn("No changes to save", self.text(app, "#message"))
                    else:
                        self.assertEqual(self.lines(app, "choices")[0], "1. Install")

    async def test_unmanaged_collision_is_reported_once_and_disables_install(self) -> None:
        unmanaged = self.home / ".agents/skills/prototype"
        unmanaged.mkdir(parents=True)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            message = self.text(app, "#message")
            reason = "unowned or changed install directory entry collision"
            self.assertEqual(message.count(reason), 1)
            self.assertIn(str(unmanaged), message)
            await pilot.press("right", "space", "space", "ctrl+s")
            self.assertEqual(self.lines(app, "choices")[0], "1. Install")
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            self.assertIn(reason, self.text(app, "#dialog-details"))
            await pilot.press("1")
            self.assertTrue(app.screen.is_modal)
            await pilot.press("escape", "space", "ctrl+s", "2")
            await app.workers.wait_for_complete()
            # The status refresh after saving must not repeat the startup error.
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
        self.assertTrue(unmanaged.is_dir())
        self.assertFalse(unmanaged.is_symlink())
        self.assertFalse((self.home / ".agents/.skillset").exists())
        self.assertFalse((self.home / ".claude").exists())

    async def test_detached_head_disables_install_and_defaults_to_save_only(self) -> None:
        git(["checkout", "-q", "--detach"], self.repo)
        await self.assert_save_only("detached HEAD")

    async def test_install_conflict_can_reload_without_exiting_or_committing(self) -> None:
        selection = self.repo / "skills.txt"
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "down", "space", "ctrl+s")
            external = "# Edited elsewhere\nmattpocock/skills:prototype\n"
            selection.write_text(external, encoding="utf-8")
            await pilot.press("1", "1")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Reloaded skills.txt. Unsaved changes discarded.")
            self.assertIn("0 unsaved", self.text(app, "#title"))
            self.assertEqual(self.lines(app, "skills"), ["● prototype", "○ tdd"])
        self.assertEqual(selection.read_text(), external)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)

    async def test_install_overwrite_rechecks_disk_then_preserves_handoff(self) -> None:
        selection = self.repo / "skills.txt"
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "down", "space", "ctrl+s")
            external = "# Edited elsewhere\nmattpocock/skills:prototype\n"
            selection.write_text(external, encoding="utf-8")
            await pilot.press("1")
            self.assertEqual(self.text(app, "#dialog-title"), "skills.txt changed on disk")
            external += "# Another edit while deciding\n"
            selection.write_text(external, encoding="utf-8")
            await pilot.press("2")
            self.assertEqual(self.text(app, "#dialog-title"), "skills.txt changed on disk")
            self.assertEqual(selection.read_text(), external)
            await pilot.press("2")
            self.assertFalse(app.is_running)
            self.assertIs(app.return_value, True)
        self.assertEqual(selection.read_text().splitlines()[1:], ["mattpocock/skills:tdd"])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), head)
        self.assertTrue((self.home / ".agents/skills/tdd").is_symlink())
        self.assertTrue((self.home / ".claude/skills/tdd").is_symlink())


if __name__ == "__main__":
    unittest.main()
