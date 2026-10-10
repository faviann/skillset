#!/usr/bin/env python3
"""Git/filesystem integration tests for the committed skillset reconciler."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from fixture import CHECKOUT, SkillsetFixture, git, write_selection, write_skill

sys.path.insert(0, str(CHECKOUT / "scripts"))
from reconcile_skills import (
    AGENTS_DIR,
    CLAUDE_DIR,
    InstallStatus,
    ReconcileError,
    build_plan,
    ensure_skillset_committed,
    install_status,
    selection_install_blocker,
)


class ReconcileIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-test-")
        self.base = Path(self.temp.name)
        self.alpha = "acme/skills:alpha"
        self.beta = "acme/skills:beta"
        self.selection = [self.alpha]
        self.fixture = SkillsetFixture(self.base, {
            "acme/skills": {
                "skills/engineering/alpha": "alpha",
                "skills/engineering/beta": "beta",
                "skills/misc/unselected": "unselected",
            },
        }, self.selection)
        self.home = self.fixture.home
        self.repo = self.fixture.repo
        self.origin = self.fixture.origins["acme/skills"]
        self.source = self.fixture.sources["acme/skills"]
        self.initial_source_commit = git(["rev-parse", "HEAD"], self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_selection(self) -> None:
        write_selection(self.repo, self.selection)

    def commit_skillset(self, message: str = "update skillset") -> None:
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", message], self.repo)

    def commit_source(self, message: str = "update source", *, pin: bool = True) -> str:
        git(["add", "-A"], self.source)
        git(["commit", "-qm", message], self.source)
        revision = git(["rev-parse", "HEAD"], self.source)
        if pin:
            git(["add", "sources/acme/skills"], self.repo)
            self.commit_skillset("advance source pin")
        return revision

    def run_reconciler(
        self,
        *args: str,
        repo: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        checkout = repo or self.repo
        env = self.fixture.env.copy()
        env["HOME"] = str(self.home)
        env.update(extra_env or {})
        failure = env.pop("SKILLSET_TEST_FAIL_AT", "")
        replacement = env.pop("SKILLSET_TEST_REPLACE_BEFORE_REMOVE", "")
        command = [str(checkout / "scripts/reconcile-skills.sh"), *args]
        if failure or replacement:
            # Faults belong to this subprocess test driver, never production flags.
            driver = r"""
import importlib.util, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
spec = importlib.util.spec_from_file_location('reconciler_under_test', sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
failure, replacement = sys.argv[2:4]
entry = Path(os.environ['HOME']) / '.agents/skills/alpha'
receipt = Path(os.environ['HOME']) / '.agents/.skillset/receipts/alpha'
original_sync = m.fsync_dir
published = False
original_link = m.os.link
def link(*args, **kwargs):
    global published
    result = original_link(*args, **kwargs)
    published = True
    return result
m.os.link = link
def sync(path):
    original_sync(path)
    if failure == 'after-receipt-create' and Path(path) == receipt.parent and receipt.is_symlink() and not entry.is_symlink():
        os._exit(73)
    if failure == 'after-install-entry-remove' and Path(path) == entry.parent and receipt.is_symlink() and not entry.is_symlink():
        os._exit(73)
    if failure == 'after-install-entry-publish' and published:
        os._exit(73)
m.fsync_dir = sync
original_apply = m.apply_plan
def apply(plan):
    if replacement:
        victim = plan.removals[0].path
        victim.unlink()
        victim.symlink_to(replacement)
    return original_apply(plan)
m.apply_plan = apply
raise SystemExit(m.main(sys.argv[4:]))
"""
            command = [sys.executable, "-c", driver,
                       str(checkout / "scripts/reconcile_skills.py"), failure, replacement, *args]
        return subprocess.run(command, cwd=checkout, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False, timeout=30)

    def assert_reconcile_fails(self, result: subprocess.CompletedProcess[str], text: str) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(text, result.stdout + result.stderr)

    def assert_link(self, install_dir: Path, name: str, target: Path) -> None:
        link = self.home / install_dir / name
        self.assertTrue(link.is_symlink(), str(link))
        self.assertEqual(os.readlink(link), str(target))

    def test_projects_only_committed_selection_and_converges(self) -> None:
        first = self.run_reconciler()
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = self.source / "skills/engineering/alpha"
        self.assert_link(AGENTS_DIR, "alpha", expected)
        self.assert_link(CLAUDE_DIR, "alpha", expected)
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assertFalse((self.home / install_dir / "unselected").exists())
        self.assertIn("created 2 skill links", first.stdout)

        second = self.run_reconciler()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "skills are reconciled")
        check = self.run_reconciler("--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn(git(["rev-parse", "HEAD"], self.repo), check.stdout)

    def test_install_links_the_selector_command_to_the_live_checkout(self) -> None:
        command = self.home / ".local/bin/select-skills"
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.assertEqual(command.resolve(), self.repo / "scripts/select-skills.py")
        self.assertEqual(Path(f"{command}.lock").resolve(), self.repo / "scripts/select-skills.py.lock")

        command.unlink()
        self.assertEqual(self.run_reconciler("--check").returncode, 0)
        self.assertFalse(command.is_symlink())
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "skills are reconciled")
        self.assertEqual(command.resolve(), self.repo / "scripts/select-skills.py")

    def test_plan_reads_the_explicit_checkout_root(self) -> None:
        with patch.dict(os.environ, {"HOME": str(self.home)}):
            plan = build_plan(self.repo)
            self.assertEqual(plan.head, git(["rev-parse", "HEAD"], self.repo))
            self.assertEqual(
                {(record.path, record.target) for record in plan.creations},
                {(self.home / directory / "alpha", str(self.source / "skills/engineering/alpha"))
                 for directory in (AGENTS_DIR, CLAUDE_DIR)},
            )
        self.assertEqual(list(self.home.iterdir()), [])

    def test_python_entrypoint_works_from_another_directory(self) -> None:
        command = [sys.executable, str(self.repo / "scripts/reconcile_skills.py")]
        for args in ([], ["--check"]):
            result = subprocess.run(
                [*command, *args], cwd=self.base, env=self.fixture.env,
                capture_output=True, text=True, check=False, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link(AGENTS_DIR, "alpha", self.source / "skills/engineering/alpha")
        self.assert_link(CLAUDE_DIR, "alpha", self.source / "skills/engineering/alpha")

    def test_untracked_catalog_module_fails_the_gate(self) -> None:
        git(["rm", "--cached", "scripts/skill_catalog.py"], self.repo)
        git(["commit", "-qm", "untrack catalog module"], self.repo)
        result = self.run_reconciler()
        self.assert_reconcile_fails(
            result, "required install input is not tracked: scripts/skill_catalog.py",
        )
        self.assertEqual(list(self.home.iterdir()), [])

    def test_untracked_sources_manifest_fails_the_gate(self) -> None:
        git(["rm", "--cached", "sources.toml"], self.repo)
        git(["commit", "-qm", "untrack sources manifest"], self.repo)
        result = self.run_reconciler()
        self.assert_reconcile_fails(
            result, "required install input is not tracked: sources.toml",
        )
        self.assertEqual(list(self.home.iterdir()), [])

    def test_modified_catalog_module_fails_the_gate(self) -> None:
        module = self.repo / "scripts/skill_catalog.py"
        module.write_text(module.read_text(encoding="utf-8") + "\n# uncommitted change\n",
                          encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "tracked skillset changes are not committed")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_additional_submodule_requires_an_explicit_skill_selection(self) -> None:
        self.fixture.add_source("other/tool", {
            "skills/engineering/gamma": "gamma",
            "skills/engineering/not-selected": "not-selected",
        })
        self.selection.append("other/tool:gamma")
        self.write_selection()
        self.commit_skillset("add and select another source")

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.repo / "sources/other/tool/skills/engineering/gamma"
        self.assert_link(AGENTS_DIR, "gamma", expected)
        self.assert_link(CLAUDE_DIR, "gamma", expected)
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assertFalse((self.home / install_dir / "not-selected").exists())

    def test_check_is_read_only_when_links_are_missing(self) -> None:
        before = set(self.home.iterdir())
        result = self.run_reconciler("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing:", result.stderr)
        self.assertEqual(set(self.home.iterdir()), before)

    def declare_upstream(self, upstream: str) -> None:
        (self.repo / "sources.toml").write_text(f'["acme/skills"]\nupstream = "{upstream}"\n', encoding="utf-8")
        self.commit_skillset("declare fork upstream")

    def test_check_reports_a_stale_upstream_without_blocking_install(self) -> None:
        # A commit the source has but its pin does not descend from.
        git(["commit", "--allow-empty", "-qm", "rebased away"], self.source)
        stale = git(["rev-parse", "HEAD"], self.source)
        git(["reset", "-q", "--hard", self.initial_source_commit], self.source)
        self.declare_upstream(stale)
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        check = self.run_reconciler("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn(f"stale upstream in sources.toml: acme/skills: {stale}", check.stderr)

    def test_check_ignores_an_upstream_object_the_clone_lacks(self) -> None:
        self.declare_upstream("1" * 40)
        self.assertEqual(self.run_reconciler().returncode, 0)
        check = self.run_reconciler("--check")
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_check_never_initializes_or_uses_network(self) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        guard = self.base / "guard-bin"
        guard.mkdir()
        wrapper = guard / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            '[ "$GIT_NO_LAZY_FETCH" = 1 ] && [ "$GIT_OPTIONAL_LOCKS" = 0 ] || { echo missing-offline-guard >&2; exit 98; }\n'
            "case \" $* \" in\n"
            "  *' submodule update '*|*' fetch '*|*' pull '*|*' clone '*|*' ls-remote '*)"
            " echo forbidden git network/init command >&2; exit 97;;\n"
            f"esac\nexec {real_git} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        result = self.run_reconciler("--check", extra_env={"PATH": f"{guard}:{os.environ['PATH']}"})
        self.assertEqual(result.returncode, 1)  # missing links, but guard itself did not fire
        self.assertNotIn("forbidden git", result.stderr)
        self.assertIn("missing:", result.stderr)
        self.assertFalse((self.home / ".agents").exists())
        guarded = {"PATH": f"{guard}:{os.environ['PATH']}", "GIT_NO_LAZY_FETCH": "0"}
        applied = self.run_reconciler(extra_env=guarded)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        checked = self.run_reconciler("--check", extra_env=guarded)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_undeclared_repeat_stops_install_before_any_link_changes(self) -> None:
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        existing = {
            self.home / directory / "alpha": (self.home / directory / "alpha").lstat()
            for directory in (AGENTS_DIR, CLAUDE_DIR,
                              Path(".agents/.skillset/receipts"),
                              Path(".claude/.skillset/receipts"))
        }
        write_skill(self.source, "skills/productivity/alpha")
        self.commit_source(pin=True)
        # Neither repeated copy is selected. Discovery must still block the
        # planned removal of alpha and installation of beta.
        self.selection = [self.beta]
        self.write_selection()
        self.commit_skillset()

        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.run_reconciler(*args)
                self.assert_reconcile_fails(result, "undeclared repeated canonical skill names")
                self.assertIn("skills/engineering/alpha", result.stderr)
                self.assertIn("skills/productivity/alpha", result.stderr)
                for path, before in existing.items():
                    self.assertTrue(os.path.samestat(before, path.lstat()), str(path))
                    self.assertEqual(os.readlink(path), str(self.source / "skills/engineering/alpha"))
                for directory in (AGENTS_DIR, CLAUDE_DIR):
                    self.assertFalse((self.home / directory / "beta").exists())

    def test_selection_resolution_error_stops_before_any_link_changes(self) -> None:
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        existing = {
            self.home / directory / "alpha": (self.home / directory / "alpha").lstat()
            for directory in (AGENTS_DIR, CLAUDE_DIR,
                              Path(".agents/.skillset/receipts"),
                              Path(".claude/.skillset/receipts"))
        }
        # Resolving beta first must not publish it or retire alpha when a later
        # selection line cannot resolve.
        self.selection = [self.beta, "acme/skills:missing"]
        self.write_selection()
        self.commit_skillset()
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.run_reconciler(*args)
                self.assert_reconcile_fails(result, "skills.txt:3: name is missing at the pinned commit")
                for path, before in existing.items():
                    self.assertTrue(os.path.samestat(before, path.lstat()), str(path))
                    self.assertEqual(os.readlink(path), str(self.source / "skills/engineering/alpha"))
                for directory in (AGENTS_DIR, CLAUDE_DIR):
                    self.assertFalse((self.home / directory / "beta").is_symlink())

    def test_existing_install_directory_skill_identity_collision_is_detected(self) -> None:
        local = self.home / ".agents/skills/local-folder"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("---\nname: alpha\ndescription: local\n---\n", encoding="utf-8")
        self.assert_reconcile_fails(self.run_reconciler(), "existing install directory skill identity collision")
        self.assertFalse((self.home / ".claude").exists())

    def test_unowned_same_target_symlink_is_not_adopted(self) -> None:
        expected = self.source / "skills/engineering/alpha"
        link = self.home / ".agents/skills/alpha"
        link.parent.mkdir(parents=True)
        link.symlink_to(expected)

        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unowned or changed install directory entry collision")
        self.assertEqual(os.readlink(link), str(expected))
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".local").exists())

        # Removing the selection grants no ownership: the identical-target link
        # remains untouched because no receipt was ever published.
        self.selection = []
        self.write_selection()
        self.commit_skillset("remove alpha selection")
        removed = self.run_reconciler()
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(expected))

    def test_unrelated_entries_are_preserved(self) -> None:
        agents = self.home / ".agents/skills"
        agents.mkdir(parents=True)
        (agents / "local-dir").mkdir()
        (agents / "local-file").write_text("keep", encoding="utf-8")
        local_target = self.base / "local-target"
        local_target.mkdir()
        (agents / "local-link").symlink_to(local_target)

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((agents / "local-dir").is_dir())
        self.assertEqual((agents / "local-file").read_text(encoding="utf-8"), "keep")
        self.assertEqual(os.readlink(agents / "local-link"), str(local_target))

    def test_preflight_prevents_partial_changes_on_collision(self) -> None:
        self.selection = [self.alpha, self.beta]
        self.write_selection()
        self.commit_skillset()
        collision = self.home / ".agents/skills/alpha"
        collision.mkdir(parents=True)
        (collision / "marker").write_text("keep", encoding="utf-8")

        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unowned or changed install directory entry collision")
        self.assertEqual((collision / "marker").read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.home / ".agents/skills/beta").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_install_directory_and_parent_symlinks_are_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        agents = self.home / ".agents"
        agents.symlink_to(outside)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed directory is a symlink")
        self.assertEqual(list(outside.iterdir()), [])

        agents.unlink()
        install_dir = self.home / ".agents/skills"
        install_dir.parent.mkdir()
        install_dir.symlink_to(outside)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed directory is a symlink")
        self.assertEqual(list(outside.iterdir()), [])

    def test_blocking_install_directory_parent_is_rejected_before_other_changes(self) -> None:
        (self.home / ".claude").write_text("preserve", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed parent is not a directory")
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual((self.home / ".claude").read_text(encoding="utf-8"), "preserve")

    def test_deleted_selected_skill_blocks_install_until_its_line_is_removed(self) -> None:
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        stale_target = self.source / "skills/engineering/alpha"
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assertTrue((self.home / install_dir / "alpha").is_symlink())

        git(["rm", "-r", "skills/engineering/alpha"], self.source)
        self.commit_source(pin=True)
        self.assertFalse(stale_target.exists())
        failed = self.run_reconciler()
        self.assert_reconcile_fails(failed, "skills.txt:2: name is missing at the pinned commit: acme/skills:alpha")
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assert_link(install_dir, "alpha", stale_target)

        self.selection = []
        self.write_selection()
        self.commit_skillset("remove selection")

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 2 owned skill links", result.stdout)
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            link = self.home / install_dir / "alpha"
            self.assertFalse(link.is_symlink())
            self.assertFalse(link.exists())

    def test_stale_links_are_cleaned_after_source_submodule_removal(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        git(["rm", "-f", "sources/acme/skills"], self.repo)
        self.selection = []
        self.write_selection()
        self.commit_skillset("remove source")

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assertFalse((self.home / install_dir / "alpha").is_symlink())

    def test_missing_uninitialized_source_fails_without_initializing(self) -> None:
        clone = self.base / "uninitialized"
        git(["clone", "-q", str(self.repo), str(clone)], self.base)
        result = self.run_reconciler(repo=clone)
        self.assert_reconcile_fails(result, "source is missing or uninitialized")
        self.assertFalse((clone / "sources/acme/skills/.git").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_dirty_source_fails_before_install_directory_changes(self) -> None:
        (self.source / "skills/engineering/alpha/SKILL.md").write_text("changed", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "source checkout is dirty")
        self.assertFalse((self.home / ".agents").exists())

    def test_wrong_source_revision_fails_before_install_directory_changes(self) -> None:
        write_skill(self.source, "skills/misc/new-commit")
        git(["add", "skills/misc/new-commit"], self.source)
        git(["commit", "-qm", "unrecorded source update"], self.source)
        self.assertNotEqual(git(["rev-parse", "HEAD"], self.source), self.initial_source_commit)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "source revision mismatch")
        self.assertFalse((self.home / ".agents").exists())

    def test_uncommitted_aggregate_configuration_is_rejected(self) -> None:
        (self.repo / "skills.txt").write_text("# uncommitted change\n", encoding="utf-8")
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.run_reconciler(*args)
                self.assert_reconcile_fails(result, "tracked skillset changes are not committed")
                self.assertIn("skills.txt", result.stderr)
                self.assertIn("run select-skills and choose Install", result.stderr)
                self.assertEqual(list(self.home.iterdir()), [])

    def test_committed_gate_allows_only_exempt_paths(self) -> None:
        readme = self.repo / "README.md"
        readme.write_text("# Fixture\n", encoding="utf-8")
        self.commit_skillset()
        (self.repo / "skills.txt").write_text("# edited selection\n", encoding="utf-8")
        ensure_skillset_committed(self.repo, exempt={"skills.txt"})
        git(["add", "skills.txt"], self.repo)
        ensure_skillset_committed(self.repo, exempt={"skills.txt"})

        readme.write_text("# Edited fixture\n", encoding="utf-8")
        with self.assertRaises(ReconcileError) as caught:
            ensure_skillset_committed(self.repo, exempt={"skills.txt"})
        self.assertIn("README.md", str(caught.exception))
        self.assertNotIn("skills.txt", str(caught.exception))
        self.assertNotIn("run select-skills and choose Install", str(caught.exception))

    def test_committed_gate_lists_staged_and_unstaged_paths(self) -> None:
        staged_path = "README caf\u00e9.md"
        readme = self.repo / staged_path
        readme.write_text("# Fixture\n", encoding="utf-8")
        self.commit_skillset()
        readme.write_text("# Edited fixture\n", encoding="utf-8")
        git(["add", staged_path], self.repo)
        (self.repo / "skills.txt").write_text("# edited selection\n", encoding="utf-8")

        with self.assertRaises(ReconcileError) as caught:
            ensure_skillset_committed(self.repo)
        self.assertIn(staged_path, str(caught.exception))
        self.assertIn("skills.txt", str(caught.exception))
        self.assertIn("run select-skills and choose Install", str(caught.exception))

        git(["checkout", "HEAD", "--", "skills.txt"], self.repo)
        with self.assertRaises(ReconcileError) as caught:
            ensure_skillset_committed(self.repo)
        self.assertIn(staged_path, str(caught.exception))
        self.assertNotIn("run select-skills and choose Install", str(caught.exception))

    def test_staged_gitlink_difference_is_rejected_even_when_worktree_is_at_head_pin(self) -> None:
        write_skill(self.source, "skills/misc/newer")
        newer = self.commit_source(pin=False)
        git(["add", "sources/acme/skills"], self.repo)
        git(["checkout", self.initial_source_commit], self.source)
        self.assertEqual(git(["rev-parse", "HEAD"], self.source), self.initial_source_commit)
        self.assertNotEqual(git(["rev-parse", ":sources/acme/skills"], self.repo), self.initial_source_commit)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "not committed")
        self.assertNotEqual(newer, self.initial_source_commit)
        self.assertFalse((self.home / ".agents").exists())

    def test_ignored_untracked_selected_content_is_rejected(self) -> None:
        (self.source / ".gitignore").write_text("skills/engineering/shadow/\n", encoding="utf-8")
        write_skill(self.source, "skills/engineering/shadow")
        self.commit_source(pin=True)
        shadow = self.source / "skills/engineering/shadow"
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "SKILL.md").write_text("---\nname: shadow\ndescription: fixture\n---\n", encoding="utf-8")
        self.selection.append("acme/skills:shadow")
        self.write_selection()
        self.commit_skillset()
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "name is missing at the pinned commit")
        self.assertFalse((self.home / ".agents").exists())

    def test_relocation_uses_recorded_old_target_to_relink(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        moved = self.base / "moved-skillset"
        git(["clone", "-q", str(self.repo), str(moved)], self.base)
        git(["-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q"], moved)

        result = self.run_reconciler(repo=moved)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = moved / "sources/acme/skills/skills/engineering/alpha"
        self.assert_link(AGENTS_DIR, "alpha", expected)
        self.assert_link(CLAUDE_DIR, "alpha", expected)
        self.assertIn("removed 2 owned skill links and created 2 skill links", result.stdout)

    def test_moved_skill_folder_relinks_without_changing_selection(self) -> None:
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        selection = (self.repo / "skills.txt").read_text(encoding="utf-8")
        moved = self.source / "skills/productivity/alpha"
        moved.parent.mkdir()
        (self.source / "skills/engineering/alpha").rename(moved)
        self.commit_source()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / "skills.txt").read_text(encoding="utf-8"), selection)
        self.assertIn("removed 2 owned skill links and created 2 skill links", result.stdout)
        for install_dir in (AGENTS_DIR, CLAUDE_DIR):
            self.assert_link(install_dir, "alpha", moved)
        self.assertEqual(self.run_reconciler("--check").returncode, 0)

    def test_declared_codex_variant_and_canonical_claude_keep_ownership_receipts(self) -> None:
        variant = write_skill(self.source, "dist/codex/alpha")
        (self.repo / "sources.toml").write_text(
            '["acme/skills".variants]\ncodex = "dist/codex"\n', encoding="utf-8",
        )
        self.commit_source()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link(AGENTS_DIR, "alpha", variant)
        self.assert_link(CLAUDE_DIR, "alpha", self.source / "skills/engineering/alpha")
        for directory in (AGENTS_DIR, CLAUDE_DIR):
            entry = self.home / directory / "alpha"
            receipt = self.home / directory.parent / ".skillset/receipts/alpha"
            self.assertTrue(os.path.samestat(entry.lstat(), receipt.lstat()))
            self.assertEqual(os.readlink(entry), os.readlink(receipt))
        self.assertEqual(self.run_reconciler("--check").returncode, 0)
        self.assertEqual(self.run_reconciler().stdout.strip(), "skills are reconciled")

    def test_pin_updates_add_and_remove_variant_without_relinking_claude(self) -> None:
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        canonical = self.source / "skills/engineering/alpha"
        claude_entry = self.home / CLAUDE_DIR / "alpha"
        claude_receipt = self.home / ".claude/.skillset/receipts/alpha"
        before = claude_entry.lstat()
        selection = (self.repo / "skills.txt").read_text(encoding="utf-8")
        variant = write_skill(self.source, "dist/codex/alpha")
        manifest = self.repo / "sources.toml"
        manifest.write_text('["acme/skills".variants]\ncodex = "dist/codex"\n',
                            encoding="utf-8")
        self.commit_source()
        for target in (variant, canonical):
            with self.subTest(target=target):
                if target == canonical:
                    shutil.rmtree(self.source / "dist")
                    manifest.write_text("", encoding="utf-8")
                    self.commit_source()
                self.assertNotEqual(self.run_reconciler("--check").returncode, 0)
                result = self.run_reconciler()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("removed 1 owned skill links and created 1 skill links", result.stdout)
                self.assert_link(AGENTS_DIR, "alpha", target)
                self.assert_link(CLAUDE_DIR, "alpha", canonical)
                self.assertTrue(os.path.samestat(before, claude_entry.lstat()))
                self.assertTrue(os.path.samestat(before, claude_receipt.lstat()))
                self.assertEqual((self.repo / "skills.txt").read_text(encoding="utf-8"), selection)
                self.assertEqual(self.run_reconciler("--check").returncode, 0)

    def test_invalid_variant_blocks_install_and_check_regardless_of_selection(self) -> None:
        write_skill(self.source, "dist/codex/alpha")
        (self.repo / "sources.toml").write_text(
            '["acme/skills".variants]\ncodex = "dist/codex"\npi = "dist/pi"\n',
            encoding="utf-8",
        )
        for name, selection in (("alpha", [self.alpha]), ("beta", [self.alpha]),
                                ("only-variant", [])):
            with self.subTest(name=name, selection=selection):
                pi_tree = self.source / "dist/pi"
                if pi_tree.exists():
                    shutil.rmtree(pi_tree)
                invalid = write_skill(pi_tree, name)
                (invalid / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
                self.selection = selection
                self.write_selection()
                self.commit_source()
                for args in ((), ("--check",)):
                    result = self.run_reconciler(*args)
                    self.assert_reconcile_fails(result, "invalid variant skill")
                    self.assertIn(str(invalid / "SKILL.md"), result.stderr)
                    self.assertIn("unsupported frontmatter", result.stderr)
                    self.assertEqual(list(self.home.iterdir()), [])

    def test_receipt_before_publication_recovers_without_adopting(self) -> None:
        failed = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-receipt-create"})
        self.assertEqual(failed.returncode, 73)
        self.assertFalse((self.home / ".agents/skills/alpha").exists())
        receipt = self.home / ".agents/.skillset/receipts/alpha"
        self.assertTrue(receipt.is_symlink())
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.samestat(receipt.lstat(), (self.home / ".agents/skills/alpha").lstat()))

    def test_interrupted_remove_recovers_and_unlinks_receipt(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.selection = []
        self.write_selection()
        self.commit_skillset("remove selection")
        failed = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-install-entry-remove"})
        self.assertEqual(failed.returncode, 73)
        entry = self.home / ".agents/skills/alpha"
        receipt = self.home / ".agents/.skillset/receipts/alpha"
        self.assertFalse(entry.is_symlink())
        self.assertTrue(receipt.is_symlink())
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(receipt.exists())
        self.assertFalse((self.home / ".claude/.skillset/receipts/alpha").exists())

    def test_replacement_after_plan_is_not_removed(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.selection = []
        self.write_selection()
        self.commit_skillset("remove selection")
        replacement = self.base / "replacement-target"
        replacement.mkdir()
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_REPLACE_BEFORE_REMOVE": str(replacement)})
        self.assert_reconcile_fails(result, "changed during reconciliation")
        entry = self.home / ".agents/skills/alpha"
        self.assertTrue(entry.is_symlink())
        self.assertEqual(os.readlink(entry), str(replacement))

    def test_git_environment_cannot_change_primary_checkout_detection(self) -> None:
        bogus = self.base / "bogus"
        bogus.mkdir()
        git(["init", "-q"], bogus)
        result = self.run_reconciler(
            extra_env={
                "GIT_DIR": str(bogus / ".git"),
                "GIT_WORK_TREE": str(bogus),
                "GIT_COMMON_DIR": str(bogus / ".git"),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_linked_worktree_is_refused_even_with_primary_git_environment(self) -> None:
        worktree = self.base / "linked-worktree"
        git(["worktree", "add", "-q", "-b", "fixture-linked", str(worktree)], self.repo)
        result = self.run_reconciler(
            repo=worktree,
            extra_env={
                "GIT_DIR": str(self.repo / ".git"),
                "GIT_WORK_TREE": str(self.repo),
                "GIT_COMMON_DIR": str(self.repo / ".git"),
            },
        )
        self.assert_reconcile_fails(result, "skill links belong to the primary checkout")
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_historical_commit_reconstructs_its_source_gitlink_and_selection(self) -> None:
        original_selection = (self.repo / "skills.txt").read_text(encoding="utf-8")
        old_skillset_commit = git(["rev-parse", "HEAD"], self.repo)
        old_source_commit = git(["rev-parse", "HEAD"], self.source)
        old_body = (self.source / "skills/engineering/alpha/SKILL.md").read_text()
        self.assertEqual(self.run_reconciler().returncode, 0)

        (self.source / "skills/engineering/alpha/SKILL.md").write_text(old_body + "Version two\n")
        write_skill(self.source, "skills/engineering/gamma")
        self.commit_source(pin=True)
        self.selection = [self.beta]
        self.write_selection()
        self.commit_skillset("select beta")
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.assert_link(AGENTS_DIR, "beta", self.source / "skills/engineering/beta")
        self.assertFalse((self.home / ".agents/skills/alpha").exists())

        historical = self.base / "historical"
        git(["clone", "-q", str(self.repo), str(historical)], self.base)
        git(["checkout", "-q", old_skillset_commit], historical)
        git(
            ["-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive", "--checkout", "-q"],
            historical,
        )
        self.assertEqual(
            git(["rev-parse", "HEAD"], historical / "sources/acme/skills"),
            old_source_commit,
        )
        self.assertEqual(
            (historical / "skills.txt").read_text(encoding="utf-8"), original_selection
        )
        restored = self.run_reconciler(repo=historical)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assert_link(AGENTS_DIR, "alpha", historical / "sources/acme/skills/skills/engineering/alpha")
        self.assert_link(CLAUDE_DIR, "alpha", historical / "sources/acme/skills/skills/engineering/alpha")
        self.assertFalse((self.home / ".agents/skills/beta").exists())
        self.assertEqual(self.run_reconciler("--check", repo=historical).returncode, 0)
        self.assertEqual((self.home / ".agents/skills/alpha/SKILL.md").read_text(), old_body)


    def test_symlinked_unrelated_skill_identity_collision_is_detected(self) -> None:
        other = self.base / "unrelated-skill"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: alpha\ndescription: unrelated\n---\n")
        alias = self.home / ".agents/skills/local-folder"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(other)
        self.assert_reconcile_fails(self.run_reconciler(), "existing install directory skill identity collision")
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.home / ".claude").exists())

    def test_ambiguous_unrelated_metadata_cannot_hide_a_collision(self) -> None:
        other = self.home / ".agents/skills/local-folder"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text('---\nname: local\n"\\u006eame": alpha\ndescription: fixture\n---\n')
        self.assert_reconcile_fails(self.run_reconciler(), "ambiguous existing skill metadata")
        self.assertFalse((self.home / ".claude").exists())

    def test_symlinked_lock_does_not_create_or_write_its_target(self) -> None:
        lock = self.home / ".agents/.skillset/lock"
        lock.parent.mkdir(parents=True)
        target = self.base / "unrelated-lock-target"
        lock.symlink_to(target)
        self.assert_reconcile_fails(self.run_reconciler(), "symlink")
        self.assertFalse(target.exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_non_regular_lock_is_rejected_without_opening_it(self) -> None:
        lock = self.home / ".agents/.skillset/lock"
        lock.parent.mkdir(parents=True)
        os.mkfifo(lock)
        self.assert_reconcile_fails(self.run_reconciler(), "lock is not a regular file")

    def test_source_ancestor_symlink_is_rejected(self) -> None:
        owner = self.repo / "sources/acme"
        moved = self.base / "moved-owner"
        owner.rename(moved)
        owner.symlink_to(moved)
        self.assert_reconcile_fails(self.run_reconciler(), "source path traverses a symlink")
        self.assertFalse((self.home / ".agents").exists())

    def test_staged_gitlink_guard_overrides_local_ignore_setting(self) -> None:
        file = self.source / "skills/engineering/alpha/SKILL.md"
        file.write_text(file.read_text()+"changed body\n")
        self.commit_source(pin=False)
        git(["add", "sources/acme/skills"], self.repo)
        git(["checkout", "--detach", self.initial_source_commit], self.source)
        git(["config", "submodule.sources/acme/skills.ignore", "all"], self.repo)
        self.assert_reconcile_fails(self.run_reconciler(), "not committed")

    def test_nested_submodule_is_rejected_even_when_initialized(self) -> None:
        git(["-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(self.origin), "nested"], self.source)
        self.commit_source()
        self.assert_reconcile_fails(self.run_reconciler(), "nested source submodules are unsupported")
        self.assertFalse((self.home / ".agents").exists())

    def test_missing_receipt_never_reclaims_same_target_install_directory(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        entry = self.home / ".agents/skills/alpha"
        before = entry.lstat()
        (self.home / ".agents/.skillset/receipts/alpha").unlink()
        self.assert_reconcile_fails(self.run_reconciler(), "unowned or changed install directory entry collision")
        self.assertTrue(os.path.samestat(before, entry.lstat()))

    def test_corrupt_receipt_is_rejected_without_removing_install_directory(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        receipt = self.home / ".agents/.skillset/receipts/alpha"
        entry = self.home / ".agents/skills/alpha"
        before = entry.lstat()
        receipt.unlink()
        receipt.write_text("not a receipt")
        self.assert_reconcile_fails(self.run_reconciler(), "invalid ownership receipt")
        self.assertTrue(os.path.samestat(before, entry.lstat()))

    def test_backup_restore_preserving_hardlinks_preserves_ownership(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        restored = self.base / "restored-home"
        subprocess.run(["cp", "-a", str(self.home), str(restored)], check=True)
        self.home = restored
        result = self.run_reconciler("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.samestat(
            (self.home / ".agents/.skillset/receipts/alpha").lstat(),
            (self.home / ".agents/skills/alpha").lstat()))

    def test_pending_receipt_cannot_adopt_or_delete_identical_unrelated_link(self) -> None:
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-receipt-create"})
        self.assertEqual(result.returncode, 73, result.stderr)
        entry = self.home / ".agents/skills/alpha"
        entry.symlink_to(self.source / "skills/engineering/alpha")
        before = entry.lstat()
        self.assert_reconcile_fails(self.run_reconciler(), "unowned or changed install directory entry collision")
        self.selection = []
        self.write_selection()
        self.commit_skillset()
        self.assert_reconcile_fails(self.run_reconciler(), "owned install directory entry changed")
        self.assertTrue(os.path.samestat(before, entry.lstat()))

    def test_interrupted_receipt_can_switch_to_a_new_target_in_one_run(self) -> None:
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-receipt-create"})
        self.assertEqual(result.returncode, 73, result.stderr)
        moved = self.source / "skills/productivity/alpha"
        moved.parent.mkdir()
        (self.source / "skills/engineering/alpha").rename(moved)
        self.commit_source()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link(AGENTS_DIR, "alpha", moved)
        self.assertEqual(self.run_reconciler("--check").returncode, 0)

    def test_interruption_after_publication_keeps_exact_ownership(self) -> None:
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-install-entry-publish"})
        self.assertEqual(result.returncode, 73, result.stderr)
        entry = self.home / ".agents/skills/alpha"
        receipt = self.home / ".agents/.skillset/receipts/alpha"
        before = entry.lstat()
        self.assertTrue(os.path.samestat(before, receipt.lstat()))
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.assertTrue(os.path.samestat(before, entry.lstat()))
        self.assertEqual(self.run_reconciler("--check").returncode, 0)

    def test_same_target_replacement_after_preflight_is_not_removed(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        target = str(self.source / "skills/engineering/alpha")
        self.selection = []
        self.write_selection()
        self.commit_skillset()
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_REPLACE_BEFORE_REMOVE": target})
        self.assert_reconcile_fails(result, "changed during reconciliation")
        entry = self.home / ".agents/skills/alpha"
        self.assertTrue(entry.is_symlink())
        self.assertEqual(os.readlink(entry), target)

    def test_malformed_committed_gitmodules_fails_before_effects(self) -> None:
        (self.repo / ".gitmodules").write_text("[not valid config\n")
        self.commit_skillset()
        self.assert_reconcile_fails(self.run_reconciler(), "git config")
        self.assertFalse((self.home / ".agents").exists())

    def test_missing_committed_source_url_is_rejected(self) -> None:
        (self.repo / ".gitmodules").write_text('[submodule "sources/acme/skills"]\npath = sources/acme/skills\n')
        self.commit_skillset()
        self.assert_reconcile_fails(self.run_reconciler(), "no committed URL")
        self.assertFalse((self.home / ".agents").exists())

    def test_missing_or_empty_tracked_branch_is_rejected(self) -> None:
        entry = f'[submodule "sources/acme/skills"]\npath = sources/acme/skills\nurl = {self.origin}\n'
        for branch in ("", "branch =\n"):
            with self.subTest(branch=branch):
                (self.repo / ".gitmodules").write_text(entry + branch)
                self.commit_skillset()
                self.assert_reconcile_fails(self.run_reconciler(),
                                            "submodule has no tracked branch: sources/acme/skills")
                self.assertFalse((self.home / ".agents").exists())



class InstallInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="skillset-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = SkillsetFixture(self.base, {
            "acme/skills": {"skills/alpha": "alpha", "skills/beta": "beta"},
        }, ["acme/skills:alpha"])
        self.repo = self.fixture.repo
        self.home = self.fixture.home
        environment = patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def commit_readme(self) -> Path:
        readme = self.repo / "README.md"
        readme.write_text("Base\n", encoding="utf-8")
        git(["add", "README.md"], self.repo)
        git(["commit", "-qm", "add readme"], self.repo)
        return readme

    def conflicting_branches(self) -> None:
        branch = git(["branch", "--show-current"], self.repo)
        readme = self.commit_readme()
        git(["checkout", "-qb", "competing"], self.repo)
        readme.write_text("Competing edit\n", encoding="utf-8")
        git(["commit", "-qam", "edit readme on competing branch"], self.repo)
        git(["checkout", "-q", branch], self.repo)
        readme.write_text("Local edit\n", encoding="utf-8")
        git(["commit", "-qam", "edit readme locally"], self.repo)

    def test_install_status_reports_whether_head_selection_is_installed(self) -> None:
        self.assertEqual(install_status(self.repo), InstallStatus(False, None))
        result = subprocess.run([str(self.repo / "scripts/reconcile-skills.sh")], cwd=self.repo,
                                env=self.fixture.env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(install_status(self.repo), InstallStatus(True, None))

    def test_install_status_reports_collision_as_problem(self) -> None:
        local = self.home / ".agents/skills/local-folder"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("---\nname: alpha\ndescription: local\n---\n", encoding="utf-8")
        status = install_status(self.repo)
        self.assertFalse(status.installed)
        self.assertIn("existing install directory skill identity collision", status.problem)
        self.assertEqual(list((self.home / ".agents/skills").iterdir()), [local])

    @unittest.skipIf(os.geteuid() == 0, "root can traverse permission-denied directories")
    def test_install_status_reports_unreadable_install_directory_as_problem(self) -> None:
        directory = self.home / ".agents/skills"
        directory.mkdir(parents=True)
        directory.chmod(0)
        try:
            status = install_status(self.repo)
        finally:
            directory.chmod(0o700)
        self.assertFalse(status.installed)
        self.assertIn("Permission denied", status.problem)
        self.assertIn(str(directory), status.problem)

    def test_install_blocker_allows_clean_checkout_and_saved_selection(self) -> None:
        self.assertIsNone(selection_install_blocker(self.repo))
        write_selection(self.repo, ["acme/skills:beta"])
        self.assertIsNone(selection_install_blocker(self.repo))

    def test_install_blocker_names_other_tracked_changes(self) -> None:
        readme = self.commit_readme()
        readme.write_text("Uncommitted edit\n", encoding="utf-8")
        write_selection(self.repo, ["acme/skills:beta"])
        blocker = selection_install_blocker(self.repo)
        self.assertIn("tracked skillset changes are not committed", blocker)
        self.assertIn("README.md", blocker)
        self.assertNotIn("skills.txt", blocker)

    def test_install_blocker_refuses_detached_head(self) -> None:
        git(["checkout", "-q", "--detach"], self.repo)
        self.assertEqual(selection_install_blocker(self.repo),
                         "checkout is on a detached HEAD; switch to a branch")

    def test_install_blocker_refuses_merge_in_progress(self) -> None:
        self.conflicting_branches()
        git(["merge", "--no-edit", "competing"], self.repo, ok=False)
        self.assertEqual(selection_install_blocker(self.repo),
                         "a merge is in progress; finish it first")

    def test_install_blocker_refuses_rebase_in_progress(self) -> None:
        self.conflicting_branches()
        git(["rebase", "competing"], self.repo, ok=False)
        self.assertEqual(selection_install_blocker(self.repo),
                         "a rebase is in progress; finish it first")

    def test_install_blocker_refuses_linked_worktree(self) -> None:
        worktree = self.base / "linked-worktree"
        git(["worktree", "add", "-q", "-b", "fixture-linked", str(worktree)], self.repo)
        self.assertIn("skill links belong to the primary checkout", selection_install_blocker(worktree))


if __name__ == "__main__":
    unittest.main(verbosity=2)
