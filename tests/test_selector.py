"""Browse fixture catalogs through the app and reject broken checkouts at launch."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from fixture import CHECKOUT, SkillsetFixture, git, write_selection

spec = importlib.util.spec_from_file_location("select_skills", CHECKOUT / "scripts/select-skills.py")
selector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selector
spec.loader.exec_module(selector)


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-selector-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

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

    def text(self, app, query: str) -> str:
        content = app.query_one(query, selector.Static).content
        return content.plain if isinstance(content, selector.Text) else str(content)

    def lines(self, app, column: str) -> list[str]:
        return [line.removeprefix("❯ ").strip() for line in self.text(app, f"#{column} RowsView").splitlines()]

    def body_text(self, app) -> str:
        return "\n".join(str(widget.content) for widget in app.query("#skill-body Static"))

    def screen_text(self, app) -> str:
        return "".join(ElementTree.fromstring(app.export_screenshot()).itertext())

    async def test_groups_order_and_selection_counts(self) -> None:
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
        }, ["acme/skills:beta", "zebra/tools:zulu"])
        self.metadata(fixture, "acme/skills", "skills/engineering/backend/beta", "display_order: -100")
        self.metadata(fixture, "acme/skills", "skills/other/zeta", "category: Assistants\ndisplay_order: -100")
        self.metadata(fixture, "acme/skills", "skills/elsewhere/delta", "category: Assistants")
        self.metadata(fixture, "acme/skills", "skills/singleton/solo", "category: Unique")
        self.pin_changes(fixture, "acme/skills")

        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "sources"), ["skills", "acme · 1/6", "tools", "zebra · 1/1"])
            self.assertIn("2 selected", self.text(app, "#title"))
            self.assertEqual(self.lines(app, "skills"), [
                "○ root", "○ solo", "Assistants", "○ delta", "○ zeta",
                "engineering", "○ alpha", "● beta",
            ])
            await pilot.press("down")
            self.assertEqual(self.lines(app, "skills"), ["● zulu"])

    async def test_keyboard_navigation_skips_headings_and_leaves_selection_unchanged(self) -> None:
        fixture = self.fixture()
        for path in ("skills/alpha", "skills/beta"):
            self.metadata(fixture, "acme/skills", path, "category: Workflow")
        self.pin_changes(fixture, "acme/skills")
        before = (fixture.repo / "skills.txt").read_bytes()
        head = git(["rev-parse", "HEAD"], fixture.repo)
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            sources = app.query_one("#sources", selector.CatalogList)
            skills = app.query_one("#skills", selector.CatalogList)
            self.assertIs(app.focused, sources)
            self.assertEqual(skills.current, "acme/skills:alpha")
            self.assertIn("acme/skills:alpha\nNot selected", self.text(app, "#skill-metadata"))
            await pilot.press("j", "k", "h", "l")
            self.assertEqual(sources.current, "acme/skills")
            self.assertIs(app.focused, sources)
            await pilot.press("enter", "up")
            self.assertIs(app.focused, skills)
            self.assertEqual(skills.current, "acme/skills:alpha")
            await pilot.press("down", "down", "space", "enter", "ctrl+s")
            self.assertEqual(skills.current, "acme/skills:beta")
            self.assertIn("acme/skills:beta\nSelected", self.text(app, "#skill-metadata"))
            self.assertEqual(self.lines(app, "skills"), ["Workflow", "○ alpha", "● beta"])
            await pilot.press("up", "left", "down")
            self.assertIs(app.focused, sources)
            self.assertEqual(sources.current, "zebra/tools")
            self.assertEqual(skills.current, "zebra/tools:gamma")
            self.assertIn("zebra/tools:gamma\nNot selected", self.text(app, "#skill-metadata"))
            await pilot.press("right")
            self.assertIs(app.focused, skills)
            await pilot.press("escape")
            self.assertFalse(app.is_running)
        self.assertEqual((fixture.repo / "skills.txt").read_bytes(), before)
        self.assertEqual(git(["rev-parse", "HEAD"], fixture.repo), head)
        self.assertEqual(git(["status", "--porcelain"], fixture.repo), "")
        self.assertEqual(list(fixture.home.iterdir()), [])

    async def test_mouse_changes_source_and_highlights_without_selecting(self) -> None:
        fixture = self.fixture()
        before = (fixture.repo / "skills.txt").read_bytes()
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            # The second source's owner/count line belongs to the same row.
            await pilot.click("#sources RowsView", offset=(4, 3))
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "○ omega"])
            skills = app.query_one("#skills", selector.CatalogList)
            await pilot.click("#skills RowsView", offset=(2, 1))
            self.assertEqual(skills.current, "zebra/tools:omega")
            self.assertIn("zebra/tools:omega", self.text(app, "#skill-metadata"))
            self.assertIs(app.focused, skills)
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "○ omega"])
            await pilot.click("#skills RowsView", offset=(6, 0))
            self.assertEqual(skills.current, "zebra/tools:gamma")
            self.assertIn("1 selected", self.text(app, "#title"))
        self.assertEqual((fixture.repo / "skills.txt").read_bytes(), before)

    async def test_bad_display_yaml_stays_listed_while_invalid_skill_is_hidden(self) -> None:
        fixture = self.fixture({"acme/skills": {
            "skills/broken": "broken", "skills/invalid": "wrong-name", "skills/manual": "manual",
        }}, ["acme/skills:broken"])
        self.metadata(fixture, "acme/skills", "skills/broken", "description: [")
        self.metadata(fixture, "acme/skills", "skills/manual", 'description: |\n  First line.\n  Second line.\ndisable-model-invocation: true')
        self.pin_changes(fixture, "acme/skills")
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "skills"), ["● broken", "○ manual  manual"])
            self.assertEqual(self.lines(app, "sources"), ["skills", "acme · 1/2"])
            self.assertIn("description unreadable", self.text(app, "#skill-metadata"))
            self.assertIn("Fixture", self.body_text(app))
            await pilot.press("right", "down")
            details = self.text(app, "#skill-metadata")
            self.assertIn("Manual only: description not loaded into context", details)
            self.assertIn("First line.\nSecond line.", details)
            self.assertNotIn("description unreadable", details)
            await pilot.press("up")
            self.assertEqual(app.query_one("#skills", selector.CatalogList).current, "acme/skills:broken")
            self.assertIn("description unreadable", self.text(app, "#skill-metadata"))

    async def test_details_metadata_and_current_selection(self) -> None:
        fixture = self.fixture({
            "acme/skills": {"alpha": "alpha"},
            "zebra/tools": {"alpha": "alpha"},
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
        self.pin_changes(fixture, "zebra/tools")

        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.text(app, "#skill-metadata").splitlines(), [
                "alpha", "acme/skills:alpha", "Selected",
                "Manual only: description not loaded into context",
                "Same name in other sources: zebra/tools:alpha",
                "Alpha description.", str(skill_dir), "3 tracked files",
            ])
            app.selected = frozenset(["zebra/tools:alpha"])
            await app.refresh_details()
            self.assertIn("acme/skills:alpha\nNot selected", self.text(app, "#skill-metadata"))
            await pilot.press("down")
            self.assertEqual(self.text(app, "#skill-metadata").splitlines(), [
                "alpha", "zebra/tools:alpha", "Selected",
                "Same name in other sources: acme/skills:alpha",
                "Other description.", str(fixture.sources["zebra/tools"] / "alpha"),
                "1 tracked file",
            ])

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
            body = self.body_text(app)
            for text in ("BodyHeading", "A bold paragraph.", "ListEntry", "CodeExample", "EndOfSkill"):
                self.assertIn(text, body)
            self.assertNotIn("MetadataOnly", body)
            self.assertNotIn("**bold**", body)
            self.assertNotIn("EndOfSkill", self.screen_text(app))
            await pilot.press("right", "tab", "end")
            await pilot.pause()
            self.assertIn("EndOfSkill", self.screen_text(app))
            await pilot.press("right", "down")
            self.assertIn("beta", self.body_text(app))
            self.assertNotIn("EndOfSkill", self.body_text(app))
            self.assertIn("acme/skills:beta", self.text(app, "#skill-metadata"))

    async def test_empty_source_and_repository_root_skill(self) -> None:
        fixture = self.fixture({"acme/empty": {}, "zebra/root": {".": "root"}}, [])
        app = selector.SelectorApp(root=fixture.repo)
        async with app.run_test() as pilot:
            self.assertEqual(self.lines(app, "sources"), ["empty", "acme · 0/0", "root", "zebra · 0/1"])
            await pilot.press("right", "up", "down", "enter")
            self.assertIsNone(app.query_one("#skills", selector.CatalogList).current)
            self.assertEqual(self.text(app, "#skill-metadata"), "")
            self.assertEqual(self.body_text(app), "")
            await pilot.press("left", "down", "right")
            self.assertEqual(self.lines(app, "skills"), ["○ root"])
            self.assertIn(str(fixture.sources["zebra/root"]), self.text(app, "#skill-metadata"))
            self.assertIn("1 tracked file", self.text(app, "#skill-metadata"))
            await pilot.press("left", "up")
            self.assertEqual(self.text(app, "#skill-metadata"), "")
            self.assertEqual(self.body_text(app), "")

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
            self.assertEqual(self.lines(app, "sources"), ["skills", "acme · 0/2", "tools", "zebra · 1/2"])
            await pilot.press("down")
            self.assertEqual(self.lines(app, "skills"), ["○ gamma", "● omega"])


class SelectorRealDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_pinned_catalog_skill_renders(self) -> None:
        app = selector.SelectorApp(root=CHECKOUT)
        visited = set()
        async with app.run_test() as pilot:
            for source, skills in app.catalog.items():
                self.assertEqual(app.query_one("#sources", selector.CatalogList).current, source)
                await pilot.press("right")
                for skill in skills:
                    key = f"{source}:{skill.name}"
                    self.assertEqual(app.query_one("#skills", selector.CatalogList).current, key)
                    self.assertIn(key, app.query_one("#skill-metadata", selector.Static).content.plain)
                    visited.add(key)
                    await pilot.press("down")
                await pilot.press("left", "down")
            await pilot.press("escape")
            self.assertFalse(app.is_running)
        self.assertEqual(visited, {
            f"{source}:{skill.name}" for source, skills in app.catalog.items() for skill in skills
        })


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
        self.assertEqual(result.stdout, "")

    def test_invalid_sources_toml_refuses_to_open(self) -> None:
        (self.fixture.repo / "sources.toml").write_text("[invalid", encoding="utf-8")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid sources.toml:", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
