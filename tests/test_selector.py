"""Browse and edit fixture selections through the app and reject broken checkouts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from markdown_it import MarkdownIt

from fixture import CHECKOUT, SkillsetFixture, git, write_selection
from pilot import PilotTestCase, selector


class SkillsetCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-selector-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        environment = patch.dict(os.environ, HOME=str(self.base / "home"))
        environment.start()
        self.addCleanup(environment.stop)

    def fixture(self, sources=None, selection=None) -> SkillsetFixture:
        return SkillsetFixture(self.base, sources if sources is not None else {
            "acme/skills": {"skills/alpha": "alpha", "skills/beta": "beta"},
            "zebra/tools": {"tools/gamma": "gamma", "tools/omega": "omega"},
        }, selection if selection is not None else ["acme/skills:beta"])

    def metadata(self, fixture: SkillsetFixture, source: str, path: str, fields: str) -> None:
        file = fixture.sources[source] / path / "SKILL.md"
        file.write_text(f"---\nname: {file.parent.name}\n{fields}\n---\n\n# Fixture\n", encoding="utf-8")

    def pin_changes(self, fixture: SkillsetFixture, source: str) -> None:
        git(["add", "-A"], fixture.sources[source])
        git(["commit", "-qm", "update skill content"], fixture.sources[source])
        git(["add", "sources"], fixture.repo)
        git(["commit", "-qm", "update source pin"], fixture.repo)


class DisplayCatalogTests(SkillsetCase):
    def display(self, fixture: SkillsetFixture) -> dict[str, list]:
        return selector.display_catalog(selector.skill_catalog.load_catalog(fixture.repo))

    def test_sources_sort_and_skills_group_by_category_or_folder(self) -> None:
        fixture = self.fixture({
            "zebra/tools": {"zulu": "zulu"},
            "acme/skills": {
                "skills/engineering/backend/beta": "beta",
                "skills/engineering/frontend/alpha": "alpha",
                "skills/other/zeta": "zeta",
                "skills/elsewhere/delta": "delta",
                "skills/singleton/solo": "solo",
                "skills/root": "root",
            },
        }, [])
        self.metadata(fixture, "acme/skills", "skills/engineering/backend/beta", "display_order: -100")
        self.metadata(fixture, "acme/skills", "skills/other/zeta", "category: Assistants\ndisplay_order: -100")
        self.metadata(fixture, "acme/skills", "skills/elsewhere/delta", "category: Assistants")
        self.metadata(fixture, "acme/skills", "skills/singleton/solo", "category: Unique")
        self.pin_changes(fixture, "acme/skills")
        catalog = self.display(fixture)
        self.assertEqual(list(catalog), ["acme/skills", "zebra/tools"])
        self.assertEqual([(skill.group, skill.name) for skill in catalog["acme/skills"]], [
            ("", "root"), ("", "solo"), ("Assistants", "delta"), ("Assistants", "zeta"),
            ("engineering", "alpha"), ("engineering", "beta"),
        ])
        self.assertEqual([(skill.group, skill.name) for skill in catalog["zebra/tools"]], [("", "zulu")])

    def test_bad_display_yaml_stays_listed_while_invalid_skill_is_hidden(self) -> None:
        fixture = self.fixture({"acme/skills": {
            "skills/broken": "broken", "skills/invalid": "wrong-name", "skills/manual": "manual",
        }}, [])
        self.metadata(fixture, "acme/skills", "skills/broken", "description: [")
        self.metadata(fixture, "acme/skills", "skills/manual",
                      "description: |\n  First line.\n  Second line.\ndisable-model-invocation: true")
        self.pin_changes(fixture, "acme/skills")
        broken, manual = self.display(fixture)["acme/skills"]
        self.assertEqual((broken.name, broken.unreadable), ("broken", True))
        self.assertIn("# Fixture", broken.body)
        self.assertEqual((manual.name, manual.unreadable, manual.manual_only), ("manual", False, True))
        self.assertEqual(manual.description, "First line.\nSecond line.\n")

    def test_empty_source_and_repository_root_skill(self) -> None:
        fixture = self.fixture({"acme/empty": {}, "zebra/root": {".": "root"}}, [])
        catalog = self.display(fixture)
        self.assertEqual(catalog["acme/empty"], [])
        [root] = catalog["zebra/root"]
        self.assertEqual((root.name, root.group, root.path, root.file_count),
                         ("root", "", fixture.sources["zebra/root"], 1))


class SelectorTests(SkillsetCase, PilotTestCase):
    async def test_keyboard_navigation_skips_headings_and_leaves_selection_unchanged(self) -> None:
        fixture = self.fixture()
        for path in ("skills/alpha", "skills/beta"):
            self.metadata(fixture, "acme/skills", path, "category: Workflow")
        self.pin_changes(fixture, "acme/skills")
        before = (fixture.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], fixture.repo)
        subprocess.run([str(fixture.repo / "scripts/reconcile-skills.sh")],
                       env=fixture.env, capture_output=True, check=True)
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            sources = app.query_one("#sources", selector.CatalogList)
            skills = app.query_one("#skills", selector.CatalogList)
            self.assertIs(app.focused, sources)
            self.assertEqual(skills.current, "acme/skills:alpha")
            self.assertIn("○ not selected\nacme/skills:alpha", self.details(app))
            await pilot.press("enter", "up")
            self.assertIs(app.focused, skills)
            self.assertEqual(skills.current, "acme/skills:alpha")
            await pilot.press("down", "down", "space")
            self.assertEqual(skills.current, "acme/skills:beta")
            self.assertEqual(self.lines(app, "skills"), ["Workflow", "○ alpha", "○ beta"])
            self.assertIn("0 selected · 1 unsaved", self.text(app, "#title"))
            self.assertIn("○ not selected\nacme/skills:beta", self.details(app))
            await pilot.press("enter")
            self.assertEqual(self.lines(app, "skills"), ["Workflow", "○ alpha", "● beta"])
            self.assertIn("● selected\nacme/skills:beta", self.details(app))
            await pilot.press("ctrl+s")
            self.assertEqual(self.text(app, "#message"), "⎿ No changes to save.")
            self.assertFalse(app.screen.is_modal)
            await pilot.press("up", "left", "down")
            self.assertIs(app.focused, sources)
            self.assertEqual(sources.current, "zebra/tools")
            self.assertEqual(skills.current, "zebra/tools:gamma")
            self.assertIn("○ not selected\nzebra/tools:gamma", self.details(app))
            await pilot.press("right")
            self.assertIs(app.focused, skills)
            await pilot.press("escape")
            self.assertFalse(app.is_running)
        self.assertEqual((fixture.repo / "skills.txt").read_bytes(), before)
        self.assertEqual(git(["rev-parse", "HEAD"], fixture.repo), head)
        self.assertEqual(git(["status", "--porcelain"], fixture.repo), "")
        self.assertTrue((fixture.home / ".agents/skills/beta").is_symlink())

    async def test_search_filters_and_moves_to_first_matching_source(self) -> None:
        fixture = self.fixture({
            "acme/skills": {"alpha": "alpha", "beta": "beta"},
            "beta/tools": {"gamma": "gamma", "omega": "omega"},
            "zebra/more": {"gamma": "gamma"},
        })
        self.metadata(fixture, "beta/tools", "gamma",
                      "category: Maintenance\ndescription: Repair fragile builds")
        self.metadata(fixture, "beta/tools", "omega", "category: Maintenance")
        self.pin_changes(fixture, "beta/tools")
        before = (fixture.repo / "skills.txt").read_bytes()
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            search = app.query_one("#search", selector.Input)
            sources = app.query_one("#sources", selector.CatalogList)
            skills = app.query_one("#skills", selector.CatalogList)
            await pilot.press(*"GAMMA")
            self.assertIs(app.focused, search)
            self.assertEqual(search.value, "GAMMA")
            self.assertEqual(sources.current, "beta/tools")
            self.assertEqual(self.lines(app, "skills"), ["Maintenance", "○ gamma"])
            self.assertEqual(self.lines(app, "sources"), [
                "acme/skills", "1/2 · 0 matches", "beta/tools", "0/2 · 1 match",
                "zebra/more", "0/1 · 1 match",
            ])
            await pilot.press("down")
            self.assertIs(app.focused, skills)
            self.assertEqual(skills.current, "beta/tools:gamma")
            await pilot.press("space")
            self.assertIn("● selected\nbeta/tools:gamma", self.details(app))
            self.assertEqual(self.lines(app, "sources")[2:4], ["beta/tools", "1/2 · 1 match"])
            await pilot.press("/")
            self.assertIs(app.focused, search)
            self.assertEqual(search.value, "GAMMA")
            await pilot.press("escape")
            self.assertIs(app.focused, search)
            self.assertEqual(search.value, "")
            await pilot.press(*"Maintenance")
            self.assertEqual(self.lines(app, "skills"), ["Maintenance", "● gamma", "○ omega"])
            self.assertEqual(self.lines(app, "sources")[2:4], ["beta/tools", "1/2 · 2 matches"])
            await pilot.press("enter")
            self.assertIs(app.focused, skills)
            self.assertIn("● selected\nbeta/tools:gamma", self.details(app))
            await pilot.press("/", "escape", "escape", "tab")
            self.assertIs(app.focused, app.query_one("#details"))
            await pilot.press(*"fragile", "space", *"builds")
            self.assertIs(app.focused, search)
            self.assertEqual(search.value, "fragile builds")
            self.assertEqual(self.lines(app, "skills"), ["Maintenance", "● gamma"])
            await pilot.press("escape", *"acme/skills")
            self.assertEqual(sources.current, "acme/skills")
            self.assertEqual(self.lines(app, "skills"), ["○ alpha", "● beta"])
            await pilot.press("escape", *"jkh")
            self.assertEqual(search.value, "jkh")
            self.assertEqual(self.lines(app, "skills"), ["No matching skills."])
            self.assertEqual(self.text(app, "#skill-metadata"), "")
            self.assertEqual(await self.body_text(pilot), "")
            await pilot.press("escape", "escape", "escape")
            self.assertEqual(self.text(app, "#dialog-title"), "Quit without saving?")
            await pilot.press("z", "/")
            self.assertEqual(search.value, "")
            self.assertEqual(self.text(app, "#dialog-title"), "Quit without saving?")
            await pilot.press("1")
            self.assertIs(app.focused, skills)
            await pilot.press("escape", "3")
            self.assertFalse(app.is_running)
        self.assertEqual((fixture.repo / "skills.txt").read_bytes(), before)

    async def test_mouse_highlights_by_name_toggles_by_circle_and_chooses_dialog_rows(self) -> None:
        fixture = self.fixture()
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            # The second source's owner/count line belongs to the same row.
            await pilot.click("#sources RowsView", offset=(4, 3))
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "○ omega"])
            skills = app.query_one("#skills", selector.CatalogList)
            await pilot.click("#skills RowsView", offset=(6, 1))
            self.assertEqual(skills.current, "zebra/tools:omega")
            self.assertIn("zebra/tools:omega", self.text(app, "#skill-metadata"))
            self.assertIs(app.focused, skills)
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "○ omega"])
            await pilot.click("#skills RowsView", offset=(6, 0))
            self.assertEqual(skills.current, "zebra/tools:gamma")
            self.assertIn("1 selected", self.text(app, "#title"))
            await pilot.click("#skills RowsView", offset=(2, 1))
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "● omega"])
            self.assertIn("● selected\nzebra/tools:omega", self.details(app))
            await pilot.press("ctrl+s")
            self.assertTrue(await pilot.click("#choices RowsView", offset=(6, 1)))
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
        self.assertEqual((fixture.repo / "skills.txt").read_text().splitlines()[1:],
                         ["acme/skills:beta", "zebra/tools:omega"])

    async def test_details_metadata_and_current_selection(self) -> None:
        fixture = self.fixture({
            "acme/skills": {"alpha": "alpha"},
            "zebra/tools": {"alpha": "alpha", "broken": "broken"},
        }, ["acme/skills:alpha"])
        self.metadata(fixture, "acme/skills", "alpha",
                      "description: Alpha description.\ndisable-model-invocation: true")
        skill_dir = fixture.sources["acme/skills"] / "alpha"
        (skill_dir / "reference.txt").write_text("Reference\n", encoding="utf-8")
        (skill_dir / "nested").mkdir()
        (skill_dir / "nested/example.txt").write_text("Example\n", encoding="utf-8")
        (fixture.sources["acme/skills"] / "outside.txt").write_text("Outside\n", encoding="utf-8")
        self.pin_changes(fixture, "acme/skills")
        self.metadata(fixture, "zebra/tools", "alpha",
                      "description: Other description.\nuser-invocable: false")
        self.metadata(fixture, "zebra/tools", "broken", "description: [")
        self.pin_changes(fixture, "zebra/tools")

        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "skills"), ["● alpha  manual"])
            self.assertEqual(self.details(app).splitlines(), [
                "alpha", "● selected", "acme/skills:alpha",
                "Manual only: description not loaded into context",
                "Same name in other sources: zebra/tools:alpha", "",
                "Alpha description.", "3 files · acme/skills/alpha",
            ])
            await pilot.press("down", "right", "space", "1", "left", "up")
            self.assertIn("○ not selected\nacme/skills:alpha", self.details(app))
            await pilot.press("down")
            self.assertEqual(self.details(app).splitlines(), [
                "alpha", "● selected", "zebra/tools:alpha",
                "Same name in other sources: acme/skills:alpha", "",
                "Other description.", "1 file · zebra/tools/alpha",
            ])
            await pilot.press("right", "down")
            self.assertIn("○ not selected\nzebra/tools:broken\n\ndescription unreadable",
                          self.details(app))

    async def test_forked_marker_shows_skills_changed_since_upstream(self) -> None:
        fixture = self.fixture()
        upstream = git(["rev-parse", "HEAD"], fixture.sources["acme/skills"])
        self.metadata(fixture, "acme/skills", "skills/alpha", "description: Changed in the fork.")
        self.pin_changes(fixture, "acme/skills")
        # zebra/tools names an upstream object the clone does not have.
        (fixture.repo / "sources.toml").write_text(
            f'["acme/skills"]\nupstream = "{upstream}"\n["zebra/tools"]\nupstream = "{"1" * 40}"\n',
            encoding="utf-8",
        )
        git(["commit", "-qam", "declare fork upstreams"], fixture.repo)

        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.text(app, "#skill-name"), "alpha (forked)")
            await pilot.press("right", "down")
            self.assertEqual(self.text(app, "#skill-name"), "beta")
            await pilot.press("left", "down")
            self.assertEqual(app.query_one("#skills", selector.CatalogList).current, "zebra/tools:gamma")
            self.assertEqual(self.text(app, "#skill-name"), "gamma")

    async def test_full_markdown_body_can_be_scrolled_and_changes_with_highlight(self) -> None:
        fixture = self.fixture()
        file = fixture.sources["acme/skills"] / "skills/alpha/SKILL.md"
        file.write_text(
            "---\nname: alpha\ndescription: MetadataOnly\n---\n\n"
            "# BodyHeading\n\nA **bold** paragraph.\n\n- ListEntry\n\n"
            "```text\nCodeExample\n```\n\n---\n\n"
            + "\n\n".join(f"Paragraph{number}" for number in range(60))
            + "\n\nEndOfSkill\n", encoding="utf-8",
        )
        self.pin_changes(fixture, "acme/skills")
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            body = await self.body_text(pilot)
            for text in ("BodyHeading", "A bold paragraph.", "ListEntry", "CodeExample", "EndOfSkill"):
                self.assertIn(text, body)
            self.assertNotIn("MetadataOnly", body)
            self.assertNotIn("**bold**", body)
            self.assertNotIn("EndOfSkill", await self.screen_text(pilot))
            await pilot.press("right", "tab", "end")
            self.assertIn("EndOfSkill", await self.screen_text(pilot))
            await pilot.press("right", "down")
            self.assertIn("beta", await self.body_text(pilot))
            self.assertNotIn("EndOfSkill", await self.body_text(pilot))
            self.assertIn("acme/skills:beta", self.text(app, "#skill-metadata"))
            await pilot.press("up", "space")
            self.assertIn("BodyHeading", await self.body_text(pilot))
            self.assertIn("BodyHeading", await self.screen_text(pilot))

    async def test_dirty_linked_worktree_can_browse_its_own_selection(self) -> None:
        fixture = self.fixture()
        worktree = self.base / "worktree"
        git(["worktree", "add", "-q", "--detach", str(worktree)], fixture.repo)
        git(["submodule", "update", "--init", "--recursive"], worktree)
        write_selection(worktree, ["zebra/tools:omega"])
        with (worktree / ".gitignore").open("a", encoding="utf-8") as file:
            file.write("\nlocal-file\n")
        app = selector.SelectorApp(root=worktree)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "sources"), ["acme/skills", "0/2", "zebra/tools", "1/2"])
            await pilot.press("down")
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "● omega"])
            await pilot.press("ctrl+s")
            self.assertIn("primary checkout", self.text(app, "#dialog-details"))
            await pilot.press("enter")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")

    async def test_replacement_dialog_routes_decline_and_accept(self) -> None:
        fixture = self.fixture({
            "acme/skills": {"alpha": "alpha"},
            "zebra/tools": {"alpha": "alpha"},
        }, ["acme/skills:alpha"])
        before = (fixture.repo / "skills.txt").read_bytes()
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await pilot.press("down", "right", "space")
            self.assertEqual(self.text(app, "#dialog-title"), "Replace alpha?")
            self.assertEqual(self.lines(app, "choices"), [
                "1. Yes, use zebra/tools", "2. No, keep acme/skills",
            ])
            await pilot.press("2")
            self.assertEqual(self.lines(app, "skills"), ["○ alpha"])
            await pilot.press("enter", "enter")
            self.assertEqual(self.lines(app, "skills"), ["● alpha"])
            self.assertIn("1 selected · 2 unsaved", self.text(app, "#title"))
            await pilot.press("left", "up")
            self.assertEqual(self.lines(app, "skills"), ["○ alpha"])
        self.assertEqual((fixture.repo / "skills.txt").read_bytes(), before)

    async def test_off_catalog_reasons_are_shown_and_can_only_be_deselected(self) -> None:
        fixture = SkillsetFixture(self.base, {"acme/skills": {
            "alpha": "alpha", "invalid": "wrong-name", "codex/lone": "lone",
        }}, ["acme/skills:alpha"], sources_toml='["acme/skills".variants]\ncodex = "codex"\n')
        reasons = {
            "broken selection": "bad syntax",
            "unknown/source:lost": "unknown source",
            "acme/skills:missing": "name is missing at the pinned commit",
            "acme/skills:invalid": "selected name is invalid",
            "acme/skills:lone": "name is missing at the pinned commit",
        }
        write_selection(fixture.repo, ["acme/skills:alpha", *reasons])
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "sources")[0], "Not in catalog")
            for value, reason in reasons.items():
                self.assertIn(value, self.text(app, "#skills RowsView"))
                self.assertIn(reason, self.text(app, "#skills RowsView").lower())
            await pilot.press("right")
            details = self.text(app, "#skill-metadata")
            value = next(value for value in reasons if value in details)
            self.assertIn(reasons[value], details.lower())
            await pilot.press("space")
            self.assertIn(f"○ {value}", self.text(app, "#skills RowsView"))
            self.assertIn("1 unsaved", self.text(app, "#title"))
            await pilot.press("enter")
            self.assertIn(f"○ {value}", self.text(app, "#skills RowsView"))
            self.assertIn("Cannot select: " + reasons[value], self.text(app, "#message"))

    async def test_save_refuses_duplicates_then_reviews_head_changes_without_committing(self) -> None:
        fixture = self.fixture({
            "acme/skills": {"skills/alpha": "alpha", "skills/beta": "beta"},
            "zebra/tools": {"tools/alpha": "alpha", "tools/omega": "omega"},
        })
        selection = fixture.repo / "skills.txt"
        write_selection(fixture.repo, ["acme/skills:alpha", "zebra/tools:alpha", "zebra/tools:omega"])
        before = selection.read_bytes()
        head = git(["rev-parse", "HEAD"], fixture.repo)
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            self.assertEqual(self.text(app, "#message"),
                             "⎿ Cannot save: same name selected more than once: alpha")
            self.assertFalse(app.screen.is_modal)
            self.assertEqual(selection.read_bytes(), before)
            await pilot.press("down", "right", "space", "ctrl+s")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            review = self.text(app, "#dialog-details")
            self.assertIn("+ acme/skills:alpha", review)
            self.assertIn("- acme/skills:beta", review)
            self.assertEqual(self.lines(app, "choices"), ["1. Save and install", "2. Save only", "3. Keep editing"])
            await pilot.press("down", "enter")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
            self.assertIn("0 unsaved", self.text(app, "#title"))
            self.assertEqual(selection.read_text(),
                             "# Written by the skill selector. Comments and ordering are not kept.\n"
                             "acme/skills:alpha\nzebra/tools:omega\n")
            self.assertEqual(git(["status", "--porcelain"], fixture.repo), "M skills.txt")
            await pilot.press("down", "space", "left", "up", "right", "space", "down", "space")
            self.assertIn("3 unsaved", self.text(app, "#title"))
            await pilot.press("ctrl+s")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertIn("No changes from the last commit.", self.text(app, "#dialog-details"))
            await pilot.press("2")
            self.assertIn("0 unsaved", self.text(app, "#title"))
            self.assertEqual(selection.read_text().splitlines()[1:], ["acme/skills:beta"])
        self.assertEqual(git(["rev-parse", "HEAD"], fixture.repo), head)
        self.assertEqual(list(fixture.home.iterdir()), [])

    async def test_reload_replaces_unsaved_selection_and_refreshes_baselines(self) -> None:
        fixture = self.fixture()
        write_selection(fixture.repo, ["acme/skills:beta", "unknown/source:stale"])
        selection = fixture.repo / "skills.txt"
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await pilot.press(*"gamma", "enter", "space", "ctrl+s")
            external = "# Changed in another window\nzebra/tools:gamma\nunknown/source:gamma-lost\nbroken gamma\n"
            selection.write_text(external, encoding="utf-8")
            git(["add", "skills.txt"], fixture.repo)
            git(["commit", "-qm", "update selection in another window"], fixture.repo)
            await pilot.press("2")
            self.assertEqual(self.lines(app, "choices"), ["1. Reload", "2. Overwrite"])
            await pilot.press("1")
            self.assertIn("3 selected · 0 unsaved", self.text(app, "#title"))
            self.assertEqual(app.query_one("#search", selector.Input).value, "gamma")
            self.assertEqual(app.query_one("#sources", selector.CatalogList).current, "Not in catalog")
            rows = self.text(app, "#skills RowsView")
            self.assertIn("● unknown/source:gamma-lost", rows)
            self.assertIn("● broken gamma", rows)
            self.assertNotIn("stale", rows)
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+s")
            self.assertEqual(self.lines(app, "choices"), ["1. Install", "2. Save only", "3. Keep editing"])
            self.assertIn("deselect the skills under Not in catalog", self.text(app, "#dialog-details"))
            await pilot.press("escape")
            await pilot.press("space", "ctrl+s")
            self.assertIn("- unknown/source:gamma-lost", self.text(app, "#dialog-details"))
            await pilot.press("2")
            self.assertIn("0 unsaved", self.text(app, "#title"))
            self.assertEqual(selection.read_text().splitlines()[1:], ["broken gamma", "zebra/tools:gamma"])

    async def test_long_save_review_can_scroll_to_final_change_and_save(self) -> None:
        names = [f"skill-{number:02}" for number in range(20)]
        fixture = self.fixture({"acme/skills": {name: name for name in names}}, [])
        values = [f"acme/skills:{name}" for name in names]
        write_selection(fixture.repo, values)
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            self.assertNotIn(values[-1], await self.screen_text(pilot))
            await pilot.press("tab", "end")
            self.assertIn(values[-1], await self.screen_text(pilot))
            await pilot.press("2")
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
            self.assertEqual((fixture.repo / "skills.txt").read_text().splitlines()[1:], values)

    async def test_quit_can_keep_editing_cancel_review_and_save_or_discard(self) -> None:
        fixture = self.fixture()
        selection = fixture.repo / "skills.txt"
        before = selection.read_bytes()
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            await pilot.press("right", "space", "escape")
            self.assertEqual(self.text(app, "#dialog-title"), "Quit without saving?")
            self.assertEqual(self.lines(app, "choices"), [
                "1. Keep editing", "2. Review and save", "3. Quit without saving",
            ])
            await pilot.press("enter")
            self.assertTrue(app.is_running)
            self.assertIn("1 unsaved", self.text(app, "#title"))
            await pilot.press("escape", "escape")
            self.assertTrue(app.is_running)
            self.assertIn("1 unsaved", self.text(app, "#title"))
            await pilot.press("escape", "2")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            self.assertIn("+ acme/skills:alpha", self.text(app, "#dialog-details"))
            await pilot.press("3")
            self.assertTrue(app.is_running)
            self.assertIn("1 unsaved", self.text(app, "#title"))
            self.assertEqual(selection.read_bytes(), before)
            await pilot.press("escape", "2", "2")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved. Not installed yet.")
            saved = selection.read_bytes()
            self.assertEqual(saved.decode().splitlines()[1:], ["acme/skills:alpha", "acme/skills:beta"])
            await pilot.press("space", "escape", "3")
            self.assertFalse(app.is_running)
        self.assertEqual(selection.read_bytes(), saved)


class RealCatalogTests(unittest.TestCase):
    def test_every_pinned_skill_has_display_data_and_a_parsable_body(self) -> None:
        catalog = selector.skill_catalog.load_catalog(CHECKOUT)
        display = selector.display_catalog(catalog)
        self.assertEqual(
            {f"{source}:{skill.name}" for source, skills in display.items() for skill in skills},
            {f"{name}:{skill}" for name, source in catalog.sources.items() for skill in source.skills},
        )
        # The parser Textual's Markdown widget uses by default.
        parser = MarkdownIt("gfm-like")
        for source, skills in display.items():
            for skill in skills:
                with self.subTest(skill=f"{source}:{skill.name}"):
                    self.assertFalse(skill.unreadable)
                    self.assertTrue(skill.description.strip())
                    self.assertGreater(skill.file_count, 0)
                    self.assertTrue(parser.parse(skill.body))


class SelectorRealDataTests(PilotTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillset-selector-real-data-")
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, HOME=temporary.name)
        environment.start()
        self.addCleanup(environment.stop)

    async def test_each_pinned_source_opens_and_renders_its_first_skill(self) -> None:
        app = selector.SelectorApp(root=CHECKOUT)
        async with app.run_test() as pilot:
            sources = app.query_one("#sources", selector.CatalogList)
            skills = app.query_one("#skills", selector.CatalogList)
            for index, (source, catalog_skills) in enumerate(app.catalog.items()):
                if index:
                    await pilot.press("down")
                key = f"{source}:{catalog_skills[0].name}"
                self.assertEqual(sources.current, source)
                self.assertEqual(skills.current, key)
                self.assertIn(key, self.text(app, "#skill-metadata"))
                self.assertTrue(await self.body_text(pilot))
            await pilot.press("escape")
            self.assertFalse(app.is_running)


class SelectorStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-selector-startup-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = SkillsetFixture(self.base, {"acme/skills": {"alpha": "alpha"}}, [])

    def launch(self) -> subprocess.CompletedProcess:
        # The symlink and unrelated cwd exercise the entrypoint's resolved root.
        link = self.base / "select-skills"
        link.symlink_to(self.fixture.repo / "scripts/select-skills.py")
        return subprocess.run(
            [sys.executable, str(link)], cwd=self.fixture.home, env=self.fixture.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )

    def test_missing_source_refuses_to_open(self) -> None:
        shutil.rmtree(self.fixture.sources["acme/skills"])
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source is missing or uninitialized: sources/acme/skills", result.stderr)
        self.assertIn("run ./setup.sh", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
