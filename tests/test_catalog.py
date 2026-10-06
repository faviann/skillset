#!/usr/bin/env python3
"""In-process catalog tests against tracked files in real fixture repositories."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fixture import CHECKOUT, SkillsetFixture, git, write_skill

sys.path.insert(0, str(CHECKOUT / "scripts"))
from skill_catalog import (
    AGENTS_DIR,
    CLAUDE_DIR,
    SELECTION_PATH,
    Catalog,
    InvalidSkill,
    ReconcileError,
    SourceCatalog,
    discover_catalog,
    load_catalog,
    parse_modules,
    parse_selection,
    read_selection_text,
    write_selection,
)


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.acme = SourceCatalog(
            {"alpha": "/acme/alpha"}, {"broken": [InvalidSkill("/acme/broken", "bad frontmatter")]}, {},
        )

    def targets(self, text: str, **sources: SourceCatalog) -> dict[tuple[Path, str], str]:
        catalog = Catalog({"acme/skills": self.acme, **{f"other/{name}": source for name, source in sources.items()}})
        return catalog.resolve(text).install_targets()

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        resolution = Catalog({"acme/skills": self.acme}).resolve(
            "\n# chosen skills\n  # another comment\n\n acme/skills:alpha \n")
        self.assertEqual([(line.value, line.number) for line in resolution.lines], [("acme/skills:alpha", 5)])
        self.assertEqual(resolution.install_targets(), {
            (AGENTS_DIR, "alpha"): "/acme/alpha", (CLAUDE_DIR, "alpha"): "/acme/alpha",
        })

    def test_syntax_errors_including_old_paths_have_line_numbers(self) -> None:
        for line in ("sources/acme/skills/skills/engineering/alpha", "acme/skills",
                     "acme/skills:alpha:extra", "acme:team/skills:alpha",
                     "acme/skills:", "acme//skills:alpha", "../skills:alpha"):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ReconcileError, r"skills.txt:3: bad syntax"):
                    self.targets(f"# selection\n\n{line}\n")

    def test_duplicate_selection_line_has_line_number(self) -> None:
        with self.assertRaisesRegex(ReconcileError, r"skills.txt:3: duplicate selection line"):
            self.targets("acme/skills:alpha\n# repeat\nacme/skills:alpha\n")

    def test_unknown_source_has_line_number(self) -> None:
        with self.assertRaisesRegex(ReconcileError, r"skills.txt:2: unknown source: unknown/skills"):
            self.targets("# selection\nunknown/skills:alpha\n")

    def test_missing_name_has_line_number(self) -> None:
        with self.assertRaisesRegex(ReconcileError, r"skills.txt:2: name is missing at the pinned commit: acme/skills:missing"):
            self.targets("\nacme/skills:missing\n")

    def test_variant_only_name_is_missing(self) -> None:
        self.acme.variants = {"codex": {"only-variant": "/acme/dist/codex/only-variant"}}
        with self.assertRaisesRegex(ReconcileError, "skills.txt:1: name is missing at the pinned commit"):
            self.targets("acme/skills:only-variant\n")

    def test_same_name_selected_from_two_sources_has_line_number(self) -> None:
        with self.assertRaises(ReconcileError) as caught:
            self.targets("acme/skills:alpha\n\nother/tools:alpha\n",
                         tools=SourceCatalog({"alpha": "/tools/alpha"}, {}, {}))
        self.assertEqual(str(caught.exception), "skills.txt:3: same name selected from two sources: alpha\n"
                                                "  acme/skills:alpha\n  other/tools:alpha")

    def test_later_syntax_error_wins_over_earlier_catalog_error(self) -> None:
        with self.assertRaisesRegex(ReconcileError, r"skills.txt:3: bad syntax"):
            self.targets("unknown/skills:alpha\nacme/skills:missing\nnot a value\n")

    def test_resolve_keeps_every_line_with_its_own_error(self) -> None:
        lines = Catalog({"acme/skills": self.acme}).resolve(
            "acme/skills:alpha\nnot a value\nacme/skills:alpha\nunknown/source:x\n"
            "acme/skills:gone\nacme/skills:broken\n").lines
        self.assertEqual([line.number for line in lines], [1, 2, 3, 4, 5, 6])
        self.assertEqual(lines[0].error, "")
        self.assertEqual(lines[0].targets, {AGENTS_DIR: "/acme/alpha", CLAUDE_DIR: "/acme/alpha"})
        self.assertEqual([line.syntax_error for line in lines], [False, True, True, False, False, False])
        self.assertEqual([line.error for line in lines[1:]], [
            "bad syntax; expected owner/repo:name: not a value",
            "duplicate selection line: acme/skills:alpha",
            "unknown source: unknown/source",
            "name is missing at the pinned commit: acme/skills:gone",
            "selected name is invalid: acme/skills:broken\n  bad frontmatter",
        ])
        for line in lines[1:]:
            self.assertEqual(line.targets, {})

    def test_install_directories_use_fixed_harness_precedence(self) -> None:
        for declared, agents_harness, claude_harness in (
            (("opencode",), "opencode", "opencode"),
            (("opencode", "pi"), "pi", "opencode"),
            (("opencode", "pi", "claude-code", "codex"), "codex", "claude-code"),
        ):
            with self.subTest(declared=declared):
                self.acme.variants = {harness: {"alpha": f"/{harness}/alpha"} for harness in declared}
                self.assertEqual(self.targets("acme/skills:alpha\n"), {
                    (AGENTS_DIR, "alpha"): f"/{agents_harness}/alpha",
                    (CLAUDE_DIR, "alpha"): f"/{claude_harness}/alpha",
                })

    def test_gap_in_first_declared_tree_falls_back_to_canonical(self) -> None:
        self.acme.variants = {harness: {name: f"/{harness}/{name}"} for harness, name in (
            ("codex", "other"), ("pi", "alpha"), ("claude-code", "other"), ("opencode", "alpha"))}
        self.assertEqual(self.targets("acme/skills:alpha\n"), {
            (AGENTS_DIR, "alpha"): "/acme/alpha", (CLAUDE_DIR, "alpha"): "/acme/alpha",
        })


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-catalog-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = SkillsetFixture(self.base, {
            "acme/skills": {"skills/engineering/alpha": "alpha"},
        }, [])
        self.repo = self.fixture.repo
        self.source = self.fixture.sources["acme/skills"]
        self.alpha = self.source / "skills/engineering/alpha"
        self.manifest = self.repo / "sources.toml"

    def catalog(self, *, validate: bool = True) -> SourceCatalog:
        # Some sources are deliberately dirty, which load_catalog rejects before discovery.
        catalog = load_catalog(self.repo) if validate else discover_catalog(self.repo, parse_modules(self.repo))
        self.assertEqual(set(catalog.sources), {"acme/skills"})
        return catalog.sources["acme/skills"]

    def targets(self, text: str) -> dict[tuple[Path, str], str]:
        return load_catalog(self.repo).resolve(text).install_targets()

    def selection_text(self, text: str) -> None:
        (self.repo / SELECTION_PATH).write_text(text, encoding="utf-8")

    def selection(self) -> list[str]:
        return [line.value for line in parse_selection(read_selection_text(self.repo))]

    def commit_source(self) -> None:
        git(["add", "-A"], self.source)
        git(["commit", "-qm", "update source fixture"], self.source)
        git(["add", "sources/acme/skills"], self.repo)
        git(["commit", "-qm", "update source pin"], self.repo)

    def write_metadata(self, fields: str) -> None:
        (self.alpha / "SKILL.md").write_text(f"---\n{fields}\n---\n", encoding="utf-8")
        self.commit_source()

    def assert_invalid(self, name: str, directory: Path, reason: str, *, validate: bool = True) -> SourceCatalog:
        catalog = self.catalog(validate=validate)
        self.assertNotIn(name, catalog.skills)
        self.assertIn(name, catalog.invalid)
        errors = {entry.path: entry.error for entry in catalog.invalid[name]}
        self.assertIn(str(directory), errors)
        self.assertIn(reason, errors[str(directory)])
        return catalog

    def declare_variant(self, path: str = "dist/codex") -> None:
        self.manifest.write_text(
            f'["acme/skills".variants]\ncodex = "{path}"\n', encoding="utf-8",
        )

    def test_plain_identity_in_nested_folders_is_canonical(self) -> None:
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})
        self.assertEqual(catalog.variants, {})
        self.assertEqual(catalog.path, self.source)
        self.assertIn("skills/engineering/alpha/SKILL.md", catalog.tracked)

    def test_tracked_excludes_untracked_files(self) -> None:
        (self.source / "untracked.txt").write_text("not committed", encoding="utf-8")
        self.assertNotIn("untracked.txt", self.catalog(validate=False).tracked)

    def test_load_catalog_rejects_a_dirty_source(self) -> None:
        (self.source / "untracked.txt").write_text("not committed", encoding="utf-8")
        with self.assertRaisesRegex(ReconcileError, "source checkout is dirty: sources/acme/skills"):
            load_catalog(self.repo)

    def test_invalid_selected_name_has_line_number_and_validation_reason(self) -> None:
        self.write_metadata('name: "alpha"')
        with self.assertRaises(ReconcileError) as caught:
            self.targets("# selection\nacme/skills:alpha\n")
        self.assertIn("skills.txt:2: selected name is invalid: acme/skills:alpha", str(caught.exception))
        self.assertIn("unsupported frontmatter identity", str(caught.exception))
        self.assertIn(str(self.alpha / "SKILL.md"), str(caught.exception))

    def test_selection_writer_sorts_and_round_trips_even_without_catalog_entries(self) -> None:
        self.selection_text("# handwritten choices\nretired/tools:gone\n\nacme/skills:alpha\n")
        selection = self.selection()
        write_selection(self.repo, selection)
        self.assertEqual((self.repo / SELECTION_PATH).read_text(encoding="utf-8"),
                         "# Written by the skill selector. Comments and ordering are not kept.\n"
                         "acme/skills:alpha\nretired/tools:gone\n")
        self.assertEqual(set(self.selection()), set(selection))
        write_selection(self.repo, [])
        self.assertEqual((self.repo / SELECTION_PATH).read_text(encoding="utf-8"),
                         "# Written by the skill selector. Comments and ordering are not kept.\n")
        self.assertEqual(self.selection(), [])

    def test_hidden_folders_are_discovered_without_plugin_manifests(self) -> None:
        hidden = write_skill(self.source, ".agents/skills/hidden")
        plugin = write_skill(self.source, "plugins/tool/skills/plugin")
        self.commit_source()
        self.assertEqual(self.catalog().skills, {
            "alpha": str(self.alpha), "hidden": str(hidden), "plugin": str(plugin),
        })

    def test_single_skill_at_source_root_needs_no_layout_adapter(self) -> None:
        shutil.rmtree(self.source / "skills")
        write_skill(self.source, ".", identity="skills")
        self.commit_source()
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"skills": str(self.source)})
        self.assertEqual(catalog.invalid, {})

    def test_identity_must_be_plain_and_match_parent(self) -> None:
        for fields, reason in (
            ('name: "alpha"', "unsupported frontmatter identity"),
            ("name: beta", "does not match its parent"),
        ):
            with self.subTest(fields=fields):
                self.write_metadata(fields)
                self.assert_invalid("alpha", self.alpha, reason)

    def test_duplicate_identity_keys_are_invalid(self) -> None:
        self.write_metadata("name: alpha\nname: beta")
        self.assert_invalid("alpha", self.alpha, "duplicate or ambiguous identity")

    def test_ambiguous_yaml_identity_shapes_are_invalid(self) -> None:
        for fields in (
            'name: alpha\n"na\\u006de": beta',
            "name: alpha\n? name: beta",
            "name: alpha\n  continuation: beta",
        ):
            with self.subTest(fields=fields):
                self.write_metadata(fields)
                self.assert_invalid("alpha", self.alpha, "unsupported frontmatter")

    def test_escaped_first_character_of_duplicate_identity_is_invalid(self) -> None:
        self.write_metadata('name: alpha\n"\\u006eame": beta\ndescription: fixture')
        self.assert_invalid("alpha", self.alpha, "unsupported frontmatter")

    def test_multiline_description_and_nested_metadata_preserve_plain_identity(self) -> None:
        self.write_metadata(
            "# comment\nname: alpha\ndescription: |\n"
            "  Uses a filename and a name.\n  name: text, not a field\n"
            "metadata:\n  author: fixture",
        )
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})

    def test_synced_name_is_invalid_because_it_is_reserved(self) -> None:
        synced = write_skill(self.source, "skills/synced")
        self.commit_source()
        self.assert_invalid("synced", synced, "reserved by Claude Code")

    def test_parent_skill_with_nested_skill_is_invalid(self) -> None:
        nested = write_skill(self.alpha, "nested")
        self.commit_source()
        catalog = self.assert_invalid("alpha", self.alpha, "additional SKILL.md")
        self.assertEqual(catalog.skills, {"nested": str(nested)})

    def test_directory_symlink_makes_a_skill_invalid(self) -> None:
        target = write_skill(self.base, "external")
        (self.alpha / "linked").symlink_to(target, target_is_directory=True)
        self.commit_source()
        self.assert_invalid("alpha", self.alpha, "directory symlinks")

    def test_tracked_directory_symlink_exposes_no_skills(self) -> None:
        target = self.base / "external"
        write_skill(target, "hidden")
        (self.source / "linked").symlink_to(target, target_is_directory=True)
        self.commit_source()
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})

    def test_untracked_and_ignored_descriptors_are_not_discovered(self) -> None:
        (self.source / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        self.commit_source()
        write_skill(self.source, "untracked")
        write_skill(self.source, "ignored")
        catalog = self.catalog(validate=False)
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})

    def test_untracked_and_ignored_content_make_a_tracked_skill_invalid(self) -> None:
        (self.source / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        self.commit_source()
        for filename in ("extra.txt", "extra.cache"):
            with self.subTest(filename=filename):
                extra = self.alpha / filename
                extra.write_text("uncommitted payload", encoding="utf-8")
                self.assert_invalid("alpha", self.alpha, "untracked or ignored content", validate=False)
                extra.unlink()

    def test_undeclared_valid_repeat_names_both_copies(self) -> None:
        write_skill(self.source, "dist/opencode/alpha")
        self.commit_source()
        with self.assertRaises(ReconcileError) as caught:
            self.catalog()
        message = str(caught.exception)
        self.assertIn("alpha", message)
        self.assertIn("skills/engineering/alpha", message)
        self.assertIn("dist/opencode/alpha", message)

    def test_invalid_root_copy_does_not_count_as_a_repeat(self) -> None:
        canonical = write_skill(self.source, "nested/skills")
        write_skill(self.source, ".", identity="skills")
        self.commit_source()
        catalog = self.catalog()
        self.assertEqual(catalog.skills["skills"], str(canonical))
        self.assertEqual(len(catalog.invalid["skills"]), 1)
        self.assertEqual(catalog.invalid["skills"][0].path, str(self.source))
        self.assertIn("additional SKILL.md", catalog.invalid["skills"][0].error)
        self.assertEqual(self.targets("acme/skills:skills\n"), {
            (AGENTS_DIR, "skills"): str(canonical),
            (CLAUDE_DIR, "skills"): str(canonical),
        })

    def test_invalid_copies_with_same_folder_name_keep_each_reason(self) -> None:
        duplicate = write_skill(self.source, "other/alpha", identity="beta")
        self.write_metadata('name: "alpha"')
        catalog = self.assert_invalid("alpha", self.alpha, "unsupported frontmatter")
        errors = {entry.path: entry.error for entry in catalog.invalid["alpha"]}
        self.assertEqual(set(errors), {str(self.alpha), str(duplicate)})
        self.assertIn("does not match its parent", errors[str(duplicate)])

    def test_declared_variant_copies_and_variant_only_names_are_excluded(self) -> None:
        variant = write_skill(self.source, "dist/codex/other-group/alpha")
        variant_only = write_skill(self.source, "dist/codex/only-variant")
        self.commit_source()
        self.declare_variant()
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})
        self.assertEqual(catalog.variants, {"codex": {
            "alpha": str(variant), "only-variant": str(variant_only),
        }})
        self.assertEqual(self.targets("acme/skills:alpha\n"), {
            (AGENTS_DIR, "alpha"): str(variant),
            (CLAUDE_DIR, "alpha"): str(self.alpha),
        })

    def test_invalid_variant_fails_even_without_a_canonical_name_or_selection(self) -> None:
        invalid = write_skill(self.source, "dist/codex/only-variant")
        (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
        self.commit_source()
        self.declare_variant()
        with self.assertRaises(ReconcileError) as caught:
            self.catalog()
        self.assertIn("invalid variant skill", str(caught.exception))
        self.assertIn(str(invalid / "SKILL.md"), str(caught.exception))
        self.assertIn("unsupported frontmatter", str(caught.exception))

    def test_variant_uses_the_same_directory_validation_as_canonical(self) -> None:
        variant = write_skill(self.source, "dist/codex/alpha")
        write_skill(variant, "nested")
        self.commit_source()
        self.declare_variant()
        with self.assertRaisesRegex(ReconcileError, "invalid variant skill.*additional SKILL.md"):
            self.catalog()

    def test_repeated_name_within_one_variant_tree_reports_both_copies(self) -> None:
        first = write_skill(self.source, "dist/codex/first/alpha")
        second = write_skill(self.source, "dist/codex/second/alpha")
        self.commit_source()
        self.declare_variant()
        with self.assertRaises(ReconcileError) as caught:
            self.catalog()
        self.assertIn("repeated variant skill name", str(caught.exception))
        self.assertIn(str(first), str(caught.exception))
        self.assertIn(str(second), str(caught.exception))

    def test_empty_manifest_source_and_variant_tables_are_valid(self) -> None:
        for content in ("", '["acme/skills"]\n', '["acme/skills".variants]\n'):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                self.assertEqual(self.catalog().skills, {"alpha": str(self.alpha)})

    def test_sources_manifest_must_exist_as_a_regular_file(self) -> None:
        self.manifest.unlink()
        with self.assertRaisesRegex(ReconcileError, "sources.toml.*regular|sources.toml.*missing"):
            self.catalog()
        self.manifest.mkdir()
        with self.assertRaisesRegex(ReconcileError, "sources.toml.*regular"):
            self.catalog()
        self.manifest.rmdir()
        target = self.base / "manifest.toml"
        target.write_text("", encoding="utf-8")
        self.manifest.symlink_to(target)
        with self.assertRaisesRegex(ReconcileError, "sources.toml.*regular"):
            self.catalog()

    def test_sources_manifest_must_be_tracked(self) -> None:
        git(["rm", "--cached", "sources.toml"], self.repo)
        with self.assertRaisesRegex(ReconcileError, "not tracked: sources.toml"):
            self.catalog()

    def test_sources_manifest_requires_utf8_and_valid_toml(self) -> None:
        for data in (b"\xff", b'["acme/skills".variants\n'):
            with self.subTest(data=data):
                self.manifest.write_bytes(data)
                with self.assertRaisesRegex(ReconcileError, "sources.toml"):
                    self.catalog()

    def test_sources_manifest_rejects_unknown_sources_harnesses_and_fields(self) -> None:
        for content, unexpected in (
            ('["unknown/skills"]\n', "unknown/skills"),
            ('["acme/skills".variants]\nother = "dist/other"\n', "other"),
            ('["acme/skills"]\npath = "skills"\n', "path"),
        ):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                with self.assertRaises(ReconcileError) as caught:
                    self.catalog()
                self.assertIn("sources.toml", str(caught.exception))
                self.assertIn(unexpected, str(caught.exception))

    def test_upstream_is_a_full_commit_sha(self) -> None:
        upstream = git(["rev-parse", "HEAD"], self.source)
        self.manifest.write_text(f'["acme/skills"]\nupstream = "{upstream}"\n', encoding="utf-8")
        self.assertEqual(self.catalog().upstream, upstream)
        for value in ('"main"', f'"{upstream[:7]}"', f'"{upstream.upper()}"', "42"):
            with self.subTest(value=value):
                self.manifest.write_text(f'["acme/skills"]\nupstream = {value}\n', encoding="utf-8")
                with self.assertRaisesRegex(ReconcileError, "upstream must be a full commit SHA"):
                    self.catalog()

    def test_sources_manifest_requires_source_and_variants_tables(self) -> None:
        for content in (
            '"acme/skills" = "skills"\n',
            '["acme/skills"]\nvariants = "dist"\n',
        ):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ReconcileError, "sources.toml"):
                    self.catalog()

    def test_variant_paths_must_be_normalized_nonempty_source_relative_strings(self) -> None:
        for value in ('42', '[]', '""', '"."', '".."', '"/dist/codex"',
                      '"../outside"', '"dist/../codex"', '"./dist/codex"',
                      '"dist//codex"', '"dist/codex/"', "'dist\\codex'"):
            with self.subTest(value=value):
                self.manifest.write_text(
                    f'["acme/skills".variants]\ncodex = {value}\n', encoding="utf-8",
                )
                with self.assertRaisesRegex(ReconcileError, "normalized source-relative tree path"):
                    self.catalog()

    def test_variant_tree_must_exist_as_a_tracked_directory(self) -> None:
        self.declare_variant()
        with self.assertRaisesRegex(ReconcileError, "sources.toml"):
            self.catalog()
        tree = self.source / "dist/codex"
        tree.parent.mkdir()
        tree.write_text("file instead of directory", encoding="utf-8")
        self.commit_source()
        with self.assertRaisesRegex(ReconcileError, "sources.toml"):
            self.catalog()
        tree.unlink()
        self.commit_source()
        tree.mkdir()
        (tree / "README.md").write_text("untracked variant", encoding="utf-8")
        with self.assertRaisesRegex(ReconcileError, "sources.toml"):
            self.catalog(validate=False)

    def test_variant_tree_must_not_be_or_traverse_a_directory_symlink(self) -> None:
        write_skill(self.source, "ports/codex/alpha")
        self.commit_source()
        (self.source / "dist").symlink_to(self.source / "ports", target_is_directory=True)
        self.commit_source()
        for path in ("dist", "dist/codex"):
            with self.subTest(path=path):
                self.declare_variant(path)
                with self.assertRaisesRegex(ReconcileError, "sources.toml"):
                    self.catalog()

    def test_all_supported_harness_keys_are_accepted(self) -> None:
        variant = write_skill(self.source, "dist/shared/alpha")
        self.commit_source()
        harnesses = ("claude-code", "codex", "pi", "opencode")
        self.manifest.write_text(
            '["acme/skills".variants]\n'
            + "".join(f'{harness} = "dist/shared"\n' for harness in harnesses),
            encoding="utf-8",
        )
        self.assertEqual(self.catalog().variants,
                         {harness: {"alpha": str(variant)} for harness in harnesses})


if __name__ == "__main__":
    unittest.main(verbosity=2)
