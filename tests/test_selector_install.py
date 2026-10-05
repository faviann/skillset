"""Drive save-and-install through the dialog and its real terminal handoff."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture import SkillsetFixture, git, write_selection
from test_selector import selector


class SelectorInstallTests(unittest.IsolatedAsyncioTestCase):
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

    def text(self, app, query: str) -> str:
        content = app.screen.query_one(query, selector.Static).content
        return content.plain if isinstance(content, selector.Text) else str(content)

    async def choose_install(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "down", "space", "ctrl+s")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 1)
            self.assertEqual(
                [line.removeprefix("❯ ").strip()
                 for line in self.text(app, "#choices RowsView").splitlines()],
                ["1. Save and install", "2. Save only", "3. Keep editing"],
            )
            await pilot.press("enter")
            self.assertFalse(app.is_running)
            self.assertIs(app.return_value, True)

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

    async def assert_save_only(self, reason: str) -> None:
        before = (self.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "ctrl+s")
            self.assertIn(reason, self.text(app, "#dialog-details"))
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            await pilot.press("1")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertEqual((self.repo / "skills.txt").read_bytes(), before)
            self.assertTrue(await pilot.click("#choices RowsView", offset=(5, 0)))
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            await pilot.press("enter")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
            self.assertIsNone(app.return_value)
        self.assertEqual((self.repo / "skills.txt").read_text().splitlines()[1:], [])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual(list(self.home.iterdir()), [])

    def conflicting_branches(self) -> None:
        branch = git(["branch", "--show-current"], self.repo)
        readme = self.repo / "README.md"
        readme.write_text("Base\n", encoding="utf-8")
        git(["add", "README.md"], self.repo)
        git(["commit", "-qm", "add readme"], self.repo)
        git(["checkout", "-qb", "competing"], self.repo)
        readme.write_text("Competing edit\n", encoding="utf-8")
        git(["commit", "-qam", "edit readme on competing branch"], self.repo)
        git(["checkout", "-q", branch], self.repo)
        readme.write_text("Local edit\n", encoding="utf-8")
        git(["commit", "-qam", "edit readme locally"], self.repo)

    async def test_default_choice_commits_only_selection_then_installs_without_upstream_hint(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        await self.choose_install()
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), previous)
        self.assertEqual(list(self.home.iterdir()), [])

        code, output = self.handoff()

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

    async def test_install_failure_keeps_commit_and_unmanaged_directory(self) -> None:
        unmanaged = self.home / ".claude/skills/tdd"
        unmanaged.mkdir(parents=True)
        (unmanaged / "notes.txt").write_text("Keep my local work.\n", encoding="utf-8")
        previous = git(["rev-parse", "HEAD"], self.repo)
        await self.choose_install()

        code, output = self.handoff()

        self.assertEqual(code, 1, output)
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")
        self.assertIn("unowned or changed install directory entry collision", output)
        self.assertIn(str(unmanaged), output)
        self.assertFalse(unmanaged.is_symlink())
        self.assertEqual((unmanaged / "notes.txt").read_text(), "Keep my local work.\n")
        self.assertFalse((self.home / ".agents").exists())

    async def test_rejected_commit_keeps_saved_selection_and_does_not_install(self) -> None:
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho 'selection commit rejected' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        previous = git(["rev-parse", "HEAD"], self.repo)
        await self.choose_install()

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

    async def test_ahead_branch_ends_with_push_hint_and_leaves_upstream_unchanged(self) -> None:
        remote = self.base / "backup.git"
        git(["init", "--bare", "-q", str(remote)], self.base)
        git(["remote", "add", "origin", str(remote)], self.repo)
        git(["push", "-qu", "origin", "HEAD"], self.repo)
        branch = git(["branch", "--show-current"], self.repo)
        upstream = git(["rev-parse", f"refs/heads/{branch}"], remote)
        await self.choose_install()

        code, output = self.handoff()

        self.assertEqual(code, 0, output)
        self.assertEqual(output.splitlines()[-1], "Not pushed yet. To back it up: git push")
        self.assertEqual(git(["rev-parse", f"refs/heads/{branch}"], remote), upstream)
        self.assertEqual(git(["rev-list", "--count", "@{upstream}..HEAD"], self.repo), "1")

    async def test_detached_head_disables_install_and_defaults_to_save_only(self) -> None:
        git(["checkout", "-q", "--detach"], self.repo)
        await self.assert_save_only("detached HEAD")

    async def test_merge_in_progress_disables_install_and_defaults_to_save_only(self) -> None:
        self.conflicting_branches()
        result = subprocess.run(
            ["git", "merge", "--no-edit", "competing"], cwd=self.repo,
            env=self.fixture.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        await self.assert_save_only("merge is in progress")

    async def test_rebase_in_progress_disables_install_and_defaults_to_save_only(self) -> None:
        self.conflicting_branches()
        result = subprocess.run(
            ["git", "rebase", "competing"], cwd=self.repo,
            env=self.fixture.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        await self.assert_save_only("rebase is in progress")

    async def test_other_tracked_changes_disable_install_and_default_to_save_only(self) -> None:
        readme = self.repo / "README.md"
        readme.write_text("Committed readme\n", encoding="utf-8")
        git(["add", "README.md"], self.repo)
        git(["commit", "-qm", "add readme"], self.repo)
        readme.write_text("Uncommitted edit\n", encoding="utf-8")
        await self.assert_save_only("README.md")
        self.assertEqual(readme.read_text(), "Uncommitted edit\n")

    async def test_off_catalog_selection_blocks_install_until_deselected(self) -> None:
        write_selection(self.repo, ["mattpocock/skills:prototype", "unknown/source:missing"])
        before = (self.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            self.assertIn("deselect the skills under Not in catalog", self.text(app, "#dialog-details"))
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 2)
            await pilot.press("1")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertEqual((self.repo / "skills.txt").read_bytes(), before)
            await pilot.press("enter")
            self.assertTrue(app.is_running)
            self.assertIn("unknown/source:missing", (self.repo / "skills.txt").read_text())
            await pilot.press("right", "space", "ctrl+s")
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, 1)
            self.assertNotIn("unavailable", self.text(app, "#dialog-details"))
            await pilot.press("enter")
            self.assertFalse(app.is_running)
            self.assertIs(app.return_value, True)
        self.assertEqual((self.repo / "skills.txt").read_text().splitlines()[1:],
                         ["mattpocock/skills:prototype"])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_install_conflict_can_reload_without_exiting_or_committing(self) -> None:
        selection = self.repo / "skills.txt"
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "down", "space", "ctrl+s")
            external = "# Edited elsewhere\nmattpocock/skills:prototype\n"
            selection.write_text(external, encoding="utf-8")
            await pilot.press("1")
            self.assertEqual(self.text(app, "#dialog-title"), "skills.txt changed on disk")
            await pilot.press("1")
            self.assertTrue(app.is_running)
            self.assertIsNone(app.return_value)
            self.assertIn("Unsaved changes discarded", self.text(app, "#message"))
            self.assertEqual(selection.read_text(), external)
            self.assertIn("● prototype", self.text(app, "#skills RowsView"))
            self.assertIn("○ tdd", self.text(app, "#skills RowsView"))
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual(list(self.home.iterdir()), [])

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
