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
from skill_catalog import ReconcileError, SourceCatalog, discover_catalog, parse_modules


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

    def catalog(self) -> SourceCatalog:
        catalogs = discover_catalog(self.repo, parse_modules(self.repo))
        self.assertEqual(set(catalogs), {"sources/acme/skills"})
        return catalogs["sources/acme/skills"]

    def commit_source(self) -> None:
        git(["add", "-A"], self.source)
        git(["commit", "-qm", "update source fixture"], self.source)
        git(["add", "sources/acme/skills"], self.repo)
        git(["commit", "-qm", "update source pin"], self.repo)

    def write_metadata(self, fields: str) -> None:
        (self.alpha / "SKILL.md").write_text(f"---\n{fields}\n---\n", encoding="utf-8")
        self.commit_source()

    def assert_invalid(self, name: str, directory: Path, reason: str) -> SourceCatalog:
        catalog = self.catalog()
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
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})

    def test_untracked_and_ignored_content_make_a_tracked_skill_invalid(self) -> None:
        (self.source / ".gitignore").write_text("*.cache\n", encoding="utf-8")
        self.commit_source()
        for filename in ("extra.txt", "extra.cache"):
            with self.subTest(filename=filename):
                extra = self.alpha / filename
                extra.write_text("uncommitted payload", encoding="utf-8")
                self.assert_invalid("alpha", self.alpha, "untracked or ignored content")
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

    def test_invalid_copies_with_same_folder_name_keep_each_reason(self) -> None:
        duplicate = write_skill(self.source, "other/alpha", identity="beta")
        self.write_metadata('name: "alpha"')
        catalog = self.assert_invalid("alpha", self.alpha, "unsupported frontmatter")
        errors = {entry.path: entry.error for entry in catalog.invalid["alpha"]}
        self.assertEqual(set(errors), {str(self.alpha), str(duplicate)})
        self.assertIn("does not match its parent", errors[str(duplicate)])

    def test_declared_variant_copies_and_variant_only_names_are_excluded(self) -> None:
        write_skill(self.source, "dist/codex/alpha")
        write_skill(self.source, "dist/codex/only-variant")
        invalid = write_skill(self.source, "dist/codex/invalid")
        (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
        self.commit_source()
        self.declare_variant()
        catalog = self.catalog()
        self.assertEqual(catalog.skills, {"alpha": str(self.alpha)})
        self.assertEqual(catalog.invalid, {})
        self.assertEqual(catalog.variants, {"codex": "dist/codex"})

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
            self.catalog()

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
        write_skill(self.source, "dist/shared/alpha")
        self.commit_source()
        harnesses = ("claude-code", "codex", "pi", "opencode")
        self.manifest.write_text(
            '["acme/skills".variants]\n'
            + "".join(f'{harness} = "dist/shared"\n' for harness in harnesses),
            encoding="utf-8",
        )
        self.assertEqual(self.catalog().variants, {harness: "dist/shared" for harness in harnesses})


if __name__ == "__main__":
    unittest.main(verbosity=2)
