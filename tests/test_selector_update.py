"""Update the highlighted source from the selector without leaving it."""

from __future__ import annotations

import os
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
        self.fixture = SkillsetFixture(self.base, {"acme/skills": {"alpha": "alpha"}, "zebra/tools": {"gamma": "gamma"}},
                                       ["acme/skills:alpha"])
        self.repo = self.fixture.repo
        self.origin = self.base / "origin.git"
        git(["init", "-q", "--bare", "-b", "main", str(self.origin)], self.base)
        git(["remote", "add", "origin", str(self.origin)], self.repo)
        git(["push", "-q", "-u", "origin", "main"], self.repo)
        environment = patch.dict(os.environ, self.fixture.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def advance_alpha(self) -> str:
        origin = self.fixture.origins["acme/skills"]
        with (origin / "alpha/SKILL.md").open("a", encoding="utf-8") as skill:
            skill.write("\nRewritten instructions.\n")
        git(["commit", "-qam", "rewrite alpha"], origin)
        return git(["rev-parse", "HEAD"], origin)

    async def started(self, pilot) -> None:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

    async def until(self, pilot, *phases) -> selector.UpdateDialog:
        dialog = pilot.app.screen
        self.assertIsInstance(dialog, selector.UpdateDialog)
        while dialog.phase not in phases:
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()
        return dialog

    def log(self, app) -> str:
        return "\n".join(strip.text.rstrip() for strip in app.screen.query_one(selector.RichLog).lines)

    def update_hint_style(self, app) -> str:
        hints = app.query_one("#hints", selector.Static).content
        start = hints.plain.index("ctrl+u update")
        return next(str(span.style) for span in hints.spans if span.start == start)

    def origin_subjects(self) -> list[str]:
        return git(["log", "--format=%s", "main"], self.origin).splitlines()

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

    async def test_update_key_on_a_source_without_an_update_only_says_so(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+u")
            self.assertEqual(self.text(app, "#message"), "⎿ No update found for acme/skills")
            self.assertEqual(len(app.screen_stack), 1)

    async def test_the_update_hint_is_accented_only_on_a_source_with_an_update(self) -> None:
        self.advance_alpha()
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            self.assertEqual(self.update_hint_style(app), selector.ACCENT)
            await pilot.press("down")
            self.assertEqual(app.source, "zebra/tools")
            self.assertEqual(self.update_hint_style(app), selector.SECONDARY)

    async def test_review_shows_the_summary_and_diff_and_escape_discards_the_update(self) -> None:
        old, new = git(["rev-parse", "HEAD:sources/acme/skills"], self.repo), self.advance_alpha()
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+u")
            dialog = await self.until(pilot, selector.Phase.REVIEW)
            self.assertIn(f"acme/skills {old[:7]} -> {new[:7]}, 1 commit\n  changed: alpha*", self.log(app))
            await pilot.press("d")
            self.assertIs(app.screen, dialog)
            self.assertIn("+Rewritten instructions.", self.log(app))
            await pilot.press("escape")
            while app.screen is dialog:
                await pilot.pause()
            self.assertTrue(app.is_running)
        self.assertEqual(self.origin_subjects(), ["initial skillset"])
        worktree = self.fixture.home / "worktrees/skillset/update-sources"
        self.assertEqual(git(["rev-parse", "HEAD"], worktree), head)
        self.assertEqual(git(["rev-parse", "HEAD"], worktree / "sources/acme/skills"), old)

    async def test_accepting_pushes_installs_and_reloads_the_selector_in_place(self) -> None:
        new = self.advance_alpha()
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            self.assertIn("acme/skills", app.markers)
            await pilot.press("ctrl+u")
            await self.until(pilot, selector.Phase.REVIEW)
            await pilot.press("a")
            await self.until(pilot, selector.Phase.DONE, selector.Phase.FAILED)
            self.assertEqual(self.text(app, "#update-status"), "acme/skills: pushed and installed")
            self.assertEqual(self.origin_subjects()[0], f"Update acme/skills to {new[:7]}")
            self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
            self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["acme/skills"]), new)
            await pilot.press("escape")
            await self.started(pilot)
            self.assertTrue(app.is_running)
            self.assertEqual(len(app.screen_stack), 1)
            self.assertNotIn("acme/skills", app.markers)
            self.assertEqual(app.source, "acme/skills")
            self.assertEqual(self.lines(app, "sources")[:2], ["acme/skills", "1/1"])
            self.assertEqual(self.update_hint_style(app), selector.SECONDARY)


if __name__ == "__main__":
    unittest.main()
