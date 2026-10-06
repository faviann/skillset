#!/usr/bin/env python3
"""Selection draft rules against an in-memory store and hand-built catalogs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fixture import CHECKOUT, SkillsetFixture, git

sys.path.insert(0, str(CHECKOUT / "scripts"))
from selection_draft import (
    INSTALL_BLOCKED,
    Blocked,
    Changes,
    CheckoutStore,
    Conflict,
    MemoryStore,
    Refused,
    Replace,
    Saved,
    SelectionDraft,
    Toggled,
)
from skill_catalog import SELECTION_HEADER, Catalog, InvalidSkill, SourceCatalog

CATALOG = Catalog({
    "acme/skills": SourceCatalog(
        {"alpha": "/acme/alpha", "beta": "/acme/beta"},
        {"broken": [InvalidSkill("/acme/broken", "bad frontmatter")]}, {},
    ),
    "zebra/tools": SourceCatalog({"alpha": "/zebra/alpha", "omega": "/zebra/omega"}, {}, {}),
})


def selection(*values: str) -> str:
    return "\n".join([SELECTION_HEADER, *values]) + "\n"


class FailingStore(MemoryStore):
    def write(self, content: str) -> None:
        raise OSError("disk full")


class SelectionDraftTests(unittest.TestCase):
    def load(self, disk: str, head: str = "") -> tuple[SelectionDraft, MemoryStore]:
        store = MemoryStore(disk, head)
        return SelectionDraft.load(store, CATALOG), store

    def test_load_keys_catalog_skills_by_value_and_off_catalog_lines_by_number(self) -> None:
        draft, _ = self.load("# note\nacme/skills:alpha\nnot a value\nacme/skills:alpha\n"
                             "unknown/source:x\nacme/skills:gone\nacme/skills:broken\n")
        self.assertIn("acme/skills:alpha", draft)
        self.assertEqual(len(draft), 6)
        self.assertEqual(list(draft.off_catalog), [3, 4, 5, 6, 7])
        for number in draft.off_catalog:
            self.assertIn(number, draft)
        errors = {number: line.error for number, line in draft.off_catalog.items()}
        self.assertIn("bad syntax", errors[3])
        self.assertIn("duplicate selection line", errors[4])
        self.assertIn("unknown source: unknown/source", errors[5])
        self.assertIn("name is missing at the pinned commit", errors[6])
        self.assertIn("selected name is invalid", errors[7])
        self.assertIn("bad frontmatter", errors[7])
        self.assertEqual(draft.off_catalog[5].value, "unknown/source:x")
        self.assertEqual(draft.unsaved, 0)

    def test_toggle_deselects_and_selects(self) -> None:
        draft, _ = self.load(selection("acme/skills:alpha"))
        self.assertEqual(draft.toggle("acme/skills:alpha"), Toggled())
        self.assertNotIn("acme/skills:alpha", draft)
        self.assertEqual(draft.toggle("acme/skills:beta"), Toggled())
        self.assertIn("acme/skills:beta", draft)
        self.assertEqual(len(draft), 1)

    def test_off_catalog_line_can_be_deselected_but_not_reselected(self) -> None:
        draft, _ = self.load("unknown/source:x\n")
        self.assertEqual(draft.toggle(1), Toggled())
        self.assertNotIn(1, draft)
        self.assertEqual(draft.toggle(1), Refused("unknown source: unknown/source"))
        self.assertNotIn(1, draft)
        self.assertIn(1, draft.off_catalog)

    def test_selecting_a_name_held_by_another_source_asks_to_replace(self) -> None:
        draft, _ = self.load(selection("acme/skills:alpha"))
        self.assertEqual(draft.toggle("zebra/tools:alpha"), Replace("acme/skills:alpha"))
        self.assertIn("acme/skills:alpha", draft)
        self.assertNotIn("zebra/tools:alpha", draft)
        self.assertEqual(draft.toggle("zebra/tools:alpha", replace=True), Toggled())
        self.assertNotIn("acme/skills:alpha", draft)
        self.assertIn("zebra/tools:alpha", draft)
        self.assertEqual(draft.unsaved, 2)

    def test_unsaved_counts_distance_from_loaded_content(self) -> None:
        head = selection("acme/skills:alpha")
        draft, _ = self.load(selection("acme/skills:beta"), head)
        draft.toggle("zebra/tools:omega")
        self.assertEqual(draft.unsaved, 1)
        draft.toggle("zebra/tools:omega")
        self.assertEqual(draft.unsaved, 0)
        draft.toggle("acme/skills:beta")
        draft.toggle("acme/skills:alpha")
        self.assertFalse(draft.changes)
        self.assertEqual(draft.unsaved, 2)

    def test_changes_compare_the_draft_with_head(self) -> None:
        draft, _ = self.load(selection("acme/skills:alpha", "acme/skills:beta"),
                             selection("acme/skills:beta", "zebra/tools:omega", "zebra/tools:omega"))
        draft.toggle("zebra/tools:alpha", replace=True)
        changes = draft.changes
        self.assertEqual(changes.added, ("zebra/tools:alpha",))
        self.assertEqual(changes.removed, ("zebra/tools:omega", "zebra/tools:omega"))
        self.assertEqual(changes.lines(), ["+ zebra/tools:alpha", "- zebra/tools:omega", "- zebra/tools:omega"])
        self.assertEqual(changes.summary, "+1 -2")
        self.assertTrue(changes)

    def test_changes_between_needs_no_catalog(self) -> None:
        changes = Changes.between("# c\nnobody/here:x\nbad line\n", "bad line\nb/c:y\nb/c:y\n")
        self.assertEqual(changes.added, ("b/c:y", "b/c:y"))
        self.assertEqual(changes.removed, ("nobody/here:x",))
        self.assertEqual(changes.lines(), ["+ b/c:y", "+ b/c:y", "- nobody/here:x"])
        self.assertEqual(changes.summary, "+2 -1")
        self.assertFalse(Changes.between("a/b:c\n", "# other\na/b:c\n"))

    def test_duplicates_block_saving(self) -> None:
        content = selection("acme/skills:alpha", "zebra/tools:alpha", "acme/skills:beta",
                            "acme/skills:beta")
        draft, store = self.load(content)
        self.assertEqual(draft.duplicates, ("alpha", "beta"))
        self.assertEqual(draft.save(), Blocked("same name selected more than once: alpha, beta"))
        self.assertEqual(store.read(), content)
        draft.toggle(5)
        draft.toggle("zebra/tools:alpha")
        self.assertEqual(draft.duplicates, ())
        self.assertEqual(draft.save(), Saved(True))

    def test_save_writes_header_and_sorted_values_and_rebaselines(self) -> None:
        draft, store = self.load("# keep?\nzebra/tools:omega\n")
        draft.toggle("acme/skills:beta")
        self.assertEqual(draft.save(), Saved(True))
        self.assertEqual(store.read(), selection("acme/skills:beta", "zebra/tools:omega"))
        self.assertEqual(draft.unsaved, 0)
        draft.toggle("acme/skills:alpha")
        self.assertEqual(draft.save(), Saved(True))
        self.assertEqual(store.read(), selection("acme/skills:alpha", "acme/skills:beta", "zebra/tools:omega"))

    def test_conflict_then_overwrite_rechecks_the_store(self) -> None:
        draft, store = self.load(selection("acme/skills:alpha"))
        draft.toggle("acme/skills:beta")
        store.disk = "first/edit:x\n"
        conflict = draft.save()
        self.assertEqual(conflict, Conflict("first/edit:x\n"))
        self.assertEqual(store.read(), "first/edit:x\n")
        store.disk = "second/edit:x\n"
        again = draft.save(overwrite=conflict)
        self.assertEqual(again, Conflict("second/edit:x\n"))
        self.assertEqual(store.read(), "second/edit:x\n")
        self.assertEqual(draft.save(overwrite=again), Saved(True))
        self.assertEqual(store.read(), selection("acme/skills:alpha", "acme/skills:beta"))
        self.assertEqual(draft.unsaved, 0)
        self.assertEqual(draft.save(), Saved(True))

    def test_install_with_nothing_unsaved_keeps_the_stored_bytes(self) -> None:
        content = "# my comment\r\nacme/skills:alpha\r\n"
        draft, store = self.load(content)
        self.assertEqual(draft.save(install=True), Saved(False))
        self.assertEqual(store.read(), content)

    def test_install_after_overwrite_writes(self) -> None:
        draft, store = self.load(selection("acme/skills:alpha"))
        store.disk = "# edited\nacme/skills:alpha\n"
        conflict = draft.save(install=True)
        self.assertIsInstance(conflict, Conflict)
        self.assertEqual(draft.save(install=True, overwrite=conflict), Saved(True))
        self.assertEqual(store.read(), selection("acme/skills:alpha"))
        self.assertEqual(draft.save(install=True), Saved(False))

    def test_install_with_unsaved_changes_writes(self) -> None:
        draft, store = self.load("# my comment\nacme/skills:alpha\n")
        draft.toggle("acme/skills:beta")
        self.assertEqual(draft.save(install=True), Saved(True))
        self.assertEqual(store.read(), selection("acme/skills:alpha", "acme/skills:beta"))

    def test_store_write_failure_leaves_the_draft_unchanged(self) -> None:
        store = FailingStore(selection("acme/skills:alpha"))
        draft = SelectionDraft.load(store, CATALOG)
        draft.toggle("acme/skills:beta")
        with self.assertRaises(OSError):
            draft.save()
        self.assertEqual(draft.unsaved, 1)
        self.assertIn("acme/skills:beta", draft)

    def test_install_is_blocked_while_an_off_catalog_line_is_selected(self) -> None:
        draft, _ = self.load(selection("acme/skills:alpha", "unknown/source:x"))
        self.assertEqual(draft.install_blocker, INSTALL_BLOCKED)
        draft.toggle(3)
        self.assertIsNone(draft.install_blocker)


class CheckoutStoreTests(unittest.TestCase):
    def test_reads_head_and_writes_skills_txt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skillset-draft-test-") as temp:
            fixture = SkillsetFixture(Path(temp), {}, ["acme/skills:alpha"])
            store = CheckoutStore(fixture.repo)
            committed = "# fixture selection\nacme/skills:alpha\n"
            self.assertEqual(store.read(), committed)
            store.write("# new\r\nzebra/tools:omega\n")
            self.assertEqual((fixture.repo / "skills.txt").read_bytes(), b"# new\r\nzebra/tools:omega\n")
            self.assertEqual(store.read(), "# new\r\nzebra/tools:omega\n")
            self.assertEqual(store.head(), committed.rstrip("\n"))
            self.assertIn("skills.txt", git(["status", "--porcelain"], fixture.repo))


if __name__ == "__main__":
    unittest.main()
