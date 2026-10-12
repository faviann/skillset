"""Edit any profile from the selector's picker, and save, commit or use it here."""

from __future__ import annotations

import unittest

from fixture import git, write_profile
from pilot import PilotTestCase, selector
from test_selector_install import InstallCase


class ProfileCase(InstallCase, PilotTestCase):
    """This home uses workstation (prototype); llm (tdd) and the empty zeroshot-review are committed too."""

    def setUp(self) -> None:
        super().setUp()
        write_profile(self.repo, "llm", ["mattpocock/skills:tdd"])
        write_profile(self.repo, "zeroshot-review", [])
        git(["add", "profiles"], self.repo)
        git(["commit", "-qm", "add llm and zeroshot-review"], self.repo)

    async def started(self, pilot) -> None:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


class TitleTests(ProfileCase):
    async def test_title_names_the_profile_this_home_uses(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  workstation  · used here   1 selected · 0 unsaved · not installed")


class PickerTests(ProfileCase):
    async def test_ctrl_p_drops_one_row_per_profile_under_the_profile_name(self) -> None:
        write_profile(self.repo, "llm", ["mattpocock/skills:tdd", "mattpocock/skills:prototype"])
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("right", "down", "space")
            self.assertIn("ctrl+p profiles", self.text(app, "#hints"))
            await pilot.press("ctrl+p")
            self.assertIsInstance(app.screen, selector.ProfilePicker)
            self.assertEqual(self.lines(app, "choices"), [
                "llm               2 skills  not committed",
                "workstation       ● used here  1 skill  1 unsaved",
                "zeroshot-review   0 skills",
            ])
            self.assertEqual(app.screen.query_one("#choices", selector.CatalogList).current, "workstation")
            # The border and padding sit left of the pointer; the names line up under the title's.
            box = app.screen.query_one("#dialog").region
            self.assertEqual((box.x + 4, box.y), (2 + len("✻ Skill selector  "), 2))

    async def test_escape_or_a_click_outside_closes_the_picker(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p", "down", "escape")
            self.assertFalse(app.screen.is_modal)
            self.assertIn("workstation  · used here", self.text(app, "#title"))
            await pilot.press("ctrl+p")
            await pilot.click(offset=(70, 20))
            self.assertFalse(app.screen.is_modal)
            self.assertIn("workstation  · used here", self.text(app, "#title"))



class SwitchTests(ProfileCase):
    async def test_enter_edits_another_profile_without_installed_markers(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            self.assertIn("· not installed", self.text(app, "#title"))
            await pilot.press("ctrl+p", "up", "enter")
            await self.started(pilot)
            self.assertFalse(app.screen.is_modal)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  llm  · this home uses workstation   1 selected · 0 unsaved")
            self.assertEqual(self.text(app, "#message"), "⎿ Editing llm. This home uses workstation.")
            await pilot.press("right")
            self.assertEqual(self.lines(app, "skills"), ["○ prototype", "● tdd"])
            await pilot.press("ctrl+p", "down", "enter")
            await self.started(pilot)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  workstation  · used here   1 selected · 0 unsaved · not installed")
            self.assertEqual(self.text(app, "#message"), "⎿ Editing workstation.")

    async def test_a_clicked_row_is_edited_and_an_uncommitted_file_is_marked(self) -> None:
        write_profile(self.repo, "zeroshot-review", ["mattpocock/skills:prototype"])
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p")
            choices = app.screen.query_one("#choices", selector.CatalogList)
            await pilot.click(choices, offset=(4, 2))
            await self.started(pilot)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  zeroshot-review  · this home uses workstation   "
                             "1 selected · 0 unsaved · not committed")


    async def test_switching_with_unsaved_changes_asks_first(self) -> None:
        workstation = (self.repo / "profiles/workstation.txt").read_bytes()
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("right", "down", "space", "ctrl+p", "up", "enter")
            self.assertEqual(self.text(app, "#dialog-title"), "Switch to llm with unsaved changes?")
            self.assertEqual(self.lines(app, "choices"),
                             ["1. Keep editing", "2. Review and save", "3. Discard and switch"])
            self.assertEqual(self.text(app, "#dialog-details"), "+ mattpocock/skills:tdd")
            await pilot.press("1")
            self.assertFalse(app.screen.is_modal)
            self.assertIn("workstation  · used here   2 selected · 1 unsaved", self.text(app, "#title"))
            await pilot.press("ctrl+p", "up", "enter", "2")
            self.assertEqual(self.text(app, "#dialog-title"), "Save these changes?")
            await pilot.press("escape", "ctrl+p", "up", "enter", "3")
            await self.started(pilot)
            self.assertFalse(app.screen.is_modal)
            self.assertIn("llm  · this home uses workstation   1 selected · 0 unsaved", self.text(app, "#title"))
            await pilot.press("ctrl+p", "down", "enter")
            await self.started(pilot)
            self.assertIn("workstation  · used here   1 selected · 0 unsaved", self.text(app, "#title"))
        self.assertEqual((self.repo / "profiles/workstation.txt").read_bytes(), workstation)



class OtherProfileSaveTests(ProfileCase):
    """Saving a profile this home doesn't use never installs it here unless asked."""

    async def save_llm_with_prototype(self, pilot, choice: str) -> None:
        app = pilot.app
        await self.started(pilot)
        await pilot.press("ctrl+p", "up", "enter")
        await self.started(pilot)
        await pilot.press("right", "space", "ctrl+s")
        self.assertEqual(self.text(app, "#dialog-title"), "Save llm?")
        self.assertEqual(self.lines(app, "choices"),
                         ["1. Save and commit", "2. Save, commit and use it here", "3. Save only"])
        self.assertEqual(self.text(app, "#dialog-details"),
                         "+ mattpocock/skills:prototype\n\n"
                         "This home uses workstation; saving llm doesn't change what's installed here.")
        await pilot.press(choice)
        await self.started(pilot)

    def assert_llm_committed(self, previous: str) -> None:
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["log", "-1", "--format=%B"], self.repo),
                         "Update llm profile (+1 -0)\n\n+ mattpocock/skills:prototype")
        self.assertEqual(git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], self.repo),
                         "profiles/llm.txt")
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    async def test_save_and_commit_commits_only_the_profile_and_installs_nothing(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_llm_with_prototype(pilot, "1")
            self.assertFalse(app.is_running)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), previous)

        code, output = self.handoff(app.return_value)

        self.assertEqual(code, 0, output)
        self.assert_llm_committed(previous)
        self.assert_nothing_installed()
        self.assertEqual((self.home / ".agents/.skillset/profile").read_text(), "workstation\n")

    async def test_save_commit_and_use_it_here_records_and_installs_the_profile(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_llm_with_prototype(pilot, "2")
            self.assertFalse(app.is_running)

        code, output = self.handoff(app.return_value)

        self.assertEqual(code, 0, output)
        self.assert_llm_committed(previous)
        self.assertEqual((self.home / ".agents/.skillset/profile").read_text(), "llm\n")
        for directory in (".agents/skills", ".claude/skills"):
            for name in ("prototype", "tdd"):
                self.assertTrue((self.home / directory / name).is_symlink())

    async def test_save_only_writes_the_file_and_marks_it_not_committed(self) -> None:
        head = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_llm_with_prototype(pilot, "3")
            self.assertTrue(app.is_running)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved profiles/llm.txt. Not committed.")
            self.assertTrue(self.text(app, "#title").endswith("2 selected · 0 unsaved · not committed"))
        self.assertEqual((self.repo / "profiles/llm.txt").read_text().splitlines()[1:],
                         ["mattpocock/skills:prototype", "mattpocock/skills:tdd"])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assert_nothing_installed()


    async def test_conflict_dialog_names_the_edited_profile_file(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p", "up", "enter")
            await self.started(pilot)
            await pilot.press("right", "space")
            write_profile(self.repo, "llm", [])
            await pilot.press("ctrl+s", "3")
            self.assertEqual(self.text(app, "#dialog-title"), "profiles/llm.txt changed on disk")
            await pilot.press("1")
            self.assertEqual(self.text(app, "#message"), "⎿ Reloaded profiles/llm.txt. Unsaved changes discarded.")
            self.assertTrue(self.text(app, "#title").endswith("0 selected · 0 unsaved · not committed"))


class NewProfileTests(ProfileCase):
    """n and c start a profile that exists only in the selector until it is saved."""

    async def name(self, pilot, name: str) -> None:
        await pilot.press("ctrl+a", "ctrl+k", *name, "enter")

    async def test_n_asks_for_a_valid_untaken_name_and_edits_an_empty_unsaved_profile(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p")
            self.assertIn("n new", self.text(app, "#dialog-hint"))
            await pilot.press("n")
            self.assertIsInstance(app.screen, selector.NameDialog)
            self.assertEqual(self.text(app, "#dialog-title"), "New profile")
            self.assertEqual(self.text(app, "#dialog-details"),
                             "Starts empty. It exists only in the selector until you save it.")
            await self.name(pilot, "Laptop")
            self.assertEqual(self.text(app, "#name-error"),
                             "use 1-64 lowercase letters, digits or single hyphens")
            await self.name(pilot, "llm")
            self.assertEqual(self.text(app, "#name-error"), "llm already exists")
            await self.name(pilot, "laptop")
            await self.started(pilot)
            self.assertFalse(app.screen.is_modal)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  laptop  · this home uses workstation   0 selected · 1 unsaved")
            self.assertEqual(self.text(app, "#message"),
                             "⎿ Created laptop. It exists only in the selector until you save it.")
            await pilot.press("ctrl+p")
            self.assertIn("laptop            0 skills  1 unsaved", self.lines(app, "choices"))
        self.assertFalse((self.repo / "profiles/laptop.txt").exists())


    async def test_c_copies_the_highlighted_profiles_saved_lines_into_an_independent_profile(self) -> None:
        write_profile(self.repo, "llm", ["mattpocock/skills:tdd", "mattpocock/skills:prototype"])
        llm = (self.repo / "profiles/llm.txt").read_bytes()
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p", "up", "c")
            self.assertEqual(self.text(app, "#dialog-title"), "Copy llm as")
            self.assertEqual(self.text(app, "#dialog-details"),
                             "Starts as a copy of llm; the two stay independent afterwards. "
                             "It exists only in the selector until you save it.")
            await pilot.press("escape")
            self.assertFalse(app.screen.is_modal)
            self.assertIn("workstation  · used here", self.text(app, "#title"))
            await pilot.press("ctrl+p", "up", "c")
            await self.name(pilot, "llm-lite")
            await self.started(pilot)
            self.assertEqual(self.text(app, "#title"),
                             "✻ Skill selector  llm-lite  · this home uses workstation   2 selected · 1 unsaved")
            await pilot.press("right", "space", "ctrl+p")
            self.assertEqual(self.lines(app, "choices")[1], "llm-lite          2 skills  2 unsaved")
            await pilot.press("escape", "ctrl+s", "3")
            await self.started(pilot)
            self.assertEqual(self.text(app, "#message"), "⎿ Saved profiles/llm-lite.txt. Not committed.")
        self.assertEqual((self.repo / "profiles/llm-lite.txt").read_text().splitlines()[1:],
                         ["mattpocock/skills:tdd"])
        self.assertEqual((self.repo / "profiles/llm.txt").read_bytes(), llm)


    async def test_switching_away_after_the_prompt_or_quitting_drops_it_without_a_file(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("ctrl+p", "n")
            await self.name(pilot, "laptop")
            await pilot.press("ctrl+p", "down", "enter")
            self.assertEqual(self.text(app, "#dialog-title"), "Switch to llm with unsaved changes?")
            await pilot.press("1")
            self.assertIn("laptop  · this home uses workstation", self.text(app, "#title"))
            await pilot.press("ctrl+p", "down", "enter", "3")
            await self.started(pilot)
            self.assertIn("llm  · this home uses workstation", self.text(app, "#title"))
            await pilot.press("ctrl+p")
            self.assertEqual([line.split()[0] for line in self.lines(app, "choices")],
                             ["llm", "workstation", "zeroshot-review"])
            await pilot.press("escape", "ctrl+p", "c")
            await self.name(pilot, "laptop")
            await pilot.press("escape")
            self.assertEqual(self.text(app, "#dialog-title"), "Quit without saving?")
            await pilot.press("3")
            self.assertFalse(app.is_running)
        self.assertIsNone(app.return_value)
        self.assertEqual(sorted(path.name for path in (self.repo / "profiles").iterdir()),
                         ["llm.txt", "workstation.txt", "zeroshot-review.txt"])
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    async def test_a_new_profile_switches_away_from_unsaved_changes_only_after_the_prompt(self) -> None:
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.started(pilot)
            await pilot.press("right", "down", "space", "ctrl+p", "n")
            await self.name(pilot, "laptop")
            self.assertEqual(self.text(app, "#dialog-title"), "Switch to laptop with unsaved changes?")
            await pilot.press("1")
            self.assertIn("workstation  · used here   2 selected · 1 unsaved", self.text(app, "#title"))
            await pilot.press("ctrl+p", "n")
            await self.name(pilot, "laptop")
            await pilot.press("3")
            await self.started(pilot)
            self.assertIn("laptop  · this home uses workstation   0 selected · 1 unsaved", self.text(app, "#title"))


    async def save_a_copy_of_llm(self, pilot, choice: str) -> None:
        app = pilot.app
        await self.started(pilot)
        await pilot.press("ctrl+p", "up", "c")
        await self.name(pilot, "laptop")
        await self.started(pilot)
        await pilot.press("ctrl+s")
        self.assertEqual(self.text(app, "#dialog-title"), "Save laptop?")
        self.assertEqual(self.lines(app, "choices"),
                         ["1. Save and commit", "2. Save, commit and use it here", "3. Save only"])
        self.assertEqual(self.text(app, "#dialog-details"),
                         "+ mattpocock/skills:tdd\n\n"
                         "This home uses workstation; saving laptop doesn't change what's installed here.")
        await pilot.press(choice)
        await self.started(pilot)

    def assert_laptop_committed(self, previous: str) -> None:
        self.assertEqual(git(["rev-parse", "HEAD^"], self.repo), previous)
        self.assertEqual(git(["log", "-1", "--format=%B"], self.repo),
                         "Add laptop profile (+1 -0)\n\n+ mattpocock/skills:tdd")
        self.assertEqual(git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], self.repo),
                         "profiles/laptop.txt")
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")

    async def test_save_and_commit_adds_the_new_profile_file_and_installs_nothing(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_a_copy_of_llm(pilot, "1")
            self.assertFalse(app.is_running)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "?? profiles/laptop.txt")

        code, output = self.handoff(app.return_value)

        self.assertEqual(code, 0, output)
        self.assert_laptop_committed(previous)
        self.assert_nothing_installed()
        self.assertEqual((self.home / ".agents/.skillset/profile").read_text(), "workstation\n")

    async def test_save_commit_and_use_it_here_records_and_installs_the_new_profile(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_a_copy_of_llm(pilot, "2")
            self.assertFalse(app.is_running)

        code, output = self.handoff(app.return_value)

        self.assertEqual(code, 0, output)
        self.assert_laptop_committed(previous)
        self.assertEqual((self.home / ".agents/.skillset/profile").read_text(), "laptop\n")
        for directory in (".agents/skills", ".claude/skills"):
            self.assertTrue((self.home / directory / "tdd").is_symlink())
            self.assertFalse((self.home / directory / "prototype").exists())

    async def test_a_new_profile_saved_only_can_be_edited_again_and_committed_later(self) -> None:
        previous = git(["rev-parse", "HEAD"], self.repo)
        app = selector.SelectorApp(root=self.repo)
        async with app.run_test() as pilot:
            await self.save_a_copy_of_llm(pilot, "3")
            self.assertTrue(self.text(app, "#title").endswith("laptop  · this home uses workstation   "
                                                              "1 selected · 0 unsaved · not committed"))
            await pilot.press("ctrl+p", "down", "down", "enter")
            await self.started(pilot)
            await pilot.press("ctrl+p", "up", "up", "enter")
            await self.started(pilot)
            self.assertEqual(self.text(app, "#message"), "⎿ Editing laptop. This home uses workstation.")
            await pilot.press("ctrl+s")
            self.assertEqual(self.text(app, "#dialog-details").splitlines()[0], "+ mattpocock/skills:tdd")
            await pilot.press("1")
            await self.started(pilot)
            self.assertFalse(app.is_running)

        code, output = self.handoff(app.return_value)

        self.assertEqual(code, 0, output)
        self.assert_laptop_committed(previous)


if __name__ == "__main__":
    unittest.main()
