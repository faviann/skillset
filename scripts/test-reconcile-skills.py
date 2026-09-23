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


CHECKOUT = Path(__file__).resolve().parent.parent
RECONCILER_FILES = ("scripts/reconcile-skills.sh", "scripts/reconcile_skills.py")


def git(args: list[str], cwd: Path, *, ok: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if ok and result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def configure_git(repo: Path) -> None:
    git(["config", "user.name", "Skillset integration tests"], repo)
    git(["config", "user.email", "skillset-tests@example.invalid"], repo)


def write_skill(repo: Path, bucket: str, directory: str, *, identity: str | None = None) -> Path:
    skill_dir = repo / "skills" / bucket / directory
    skill_dir.mkdir(parents=True, exist_ok=True)
    name = identity or directory
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: fixture\n---\n\n# {directory}\n",
        encoding="utf-8",
    )
    return skill_dir


class ReconcileIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-test-")
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.origin = self.base / "source-origin"
        self.origin.mkdir()
        git(["init", "-q"], self.origin)
        configure_git(self.origin)
        write_skill(self.origin, "engineering", "alpha")
        write_skill(self.origin, "engineering", "beta")
        write_skill(self.origin, "misc", "unselected")
        git(["add", "skills"], self.origin)
        git(["commit", "-qm", "initial source"], self.origin)
        self.initial_source_commit = git(["rev-parse", "HEAD"], self.origin)

        self.repo = self.base / "skillset"
        self.repo.mkdir()
        git(["init", "-q"], self.repo)
        configure_git(self.repo)
        (self.repo / "scripts").mkdir()
        for name in RECONCILER_FILES:
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CHECKOUT / name, destination)
        (self.repo / "scripts/reconcile-skills.sh").chmod(0o755)
        git(
            ["-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(self.origin), "sources/acme/skills"],
            self.repo,
        )
        self.source = self.repo / "sources/acme/skills"
        configure_git(self.source)
        self.alpha = "sources/acme/skills/skills/engineering/alpha"
        self.beta = "sources/acme/skills/skills/engineering/beta"
        self.selection = [self.alpha]
        self.write_selection()
        git(["add", ".gitmodules", "sources", "skills.txt", "scripts"], self.repo)
        git(["commit", "-qm", "initial skillset"], self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_selection(self) -> None:
        content = "# fixture selection\n" + "".join(f"{path}\n" for path in self.selection)
        (self.repo / "skills.txt").write_text(content, encoding="utf-8")

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
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                del env[key]
        env["HOME"] = str(self.home)
        env.update(extra_env or {})
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        failure = env.pop("SKILLSET_TEST_FAIL_AT", "")
        replacement = env.pop("SKILLSET_TEST_REPLACE_BEFORE_REMOVE", "")
        command = [str(checkout / "scripts/reconcile-skills.sh"), *args]
        if failure or replacement:
            # Faults belong to this subprocess test driver, never production flags.
            driver = r"""
import importlib.util, os, sys
from pathlib import Path
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
    if failure == 'after-consumer-remove' and Path(path) == entry.parent and receipt.is_symlink() and not entry.is_symlink():
        os._exit(73)
    if failure == 'after-consumer-publish' and published:
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

    def assert_link(self, harness: str, name: str, target: Path) -> None:
        link = self.home / f".{harness}/skills/{name}"
        self.assertTrue(link.is_symlink(), str(link))
        self.assertEqual(os.readlink(link), str(target))

    def test_projects_only_committed_selection_and_converges(self) -> None:
        first = self.run_reconciler()
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = self.source / "skills/engineering/alpha"
        self.assert_link("agents", "alpha", expected)
        self.assert_link("claude", "alpha", expected)
        for harness in ("agents", "claude"):
            self.assertFalse((self.home / f".{harness}/skills/unselected").exists())
        self.assertIn("created 2 skill links", first.stdout)

        second = self.run_reconciler()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "skills are reconciled")
        check = self.run_reconciler("--check")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn(git(["rev-parse", "HEAD"], self.repo), check.stdout)

    def test_additional_submodule_requires_an_explicit_skill_selection(self) -> None:
        other_origin = self.base / "other-origin"
        other_origin.mkdir()
        git(["init", "-q"], other_origin)
        configure_git(other_origin)
        write_skill(other_origin, "engineering", "gamma")
        write_skill(other_origin, "engineering", "not-selected")
        git(["add", "skills"], other_origin)
        git(["commit", "-qm", "other source"], other_origin)
        git(
            ["-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(other_origin), "sources/other/tool"],
            self.repo,
        )
        self.selection.append("sources/other/tool/skills/engineering/gamma")
        self.write_selection()
        self.commit_skillset("add and select another source")

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.repo / "sources/other/tool/skills/engineering/gamma"
        self.assert_link("agents", "gamma", expected)
        self.assert_link("claude", "gamma", expected)
        for harness in ("agents", "claude"):
            self.assertFalse((self.home / f".{harness}/skills/not-selected").exists())

    def test_check_is_read_only_when_links_are_missing(self) -> None:
        before = set(self.home.iterdir())
        result = self.run_reconciler("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing:", result.stderr)
        self.assertEqual(set(self.home.iterdir()), before)

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
            "  *' submodule update '*|*' fetch '*|*' pull '*|*' clone '*) echo forbidden git network/init command >&2; exit 97;;\n"
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

    def test_selected_name_collision_fails_before_any_effect(self) -> None:
        write_skill(self.source, "productivity", "alpha")
        self.commit_source(pin=True)
        self.selection = [self.alpha, "sources/acme/skills/skills/productivity/alpha"]
        self.write_selection()
        self.commit_skillset()

        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "selected skill-name collision: alpha")
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_frontmatter_identity_must_be_plain_and_match_parent(self) -> None:
        skill_file = self.source / "skills/engineering/alpha/SKILL.md"
        skill_file.write_text("---\nname: \"alpha\"\ndescription: fixture\n---\n", encoding="utf-8")
        self.commit_source(pin=True)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unsupported frontmatter identity")
        self.assertFalse((self.home / ".agents").exists())

    def test_duplicate_frontmatter_identity_fails_closed(self) -> None:
        skill_file = self.source / "skills/engineering/alpha/SKILL.md"
        skill_file.write_text("---\nname: alpha\nname: beta\n---\n", encoding="utf-8")
        self.commit_source(pin=True)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unsupported")

    def test_ambiguous_yaml_identity_shapes_fail_closed(self) -> None:
        cases = [
            'name: alpha\n"na\\u006de": beta',
            "name: alpha\n? name: beta",
            "name: alpha\n  continuation: beta",
        ]
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields):
                file = self.source / "skills/engineering/alpha/SKILL.md"
                file.write_text(f"---\n{fields}\ndescription: fixture\n---\n", encoding="utf-8")
                self.commit_source(pin=True)
                result = self.run_reconciler()
                self.assert_reconcile_fails(result, "unsupported")
                # Restore a valid source revision before the next subcase.
                file.write_text("---\nname: alpha\ndescription: fixture\n---\n", encoding="utf-8")
                self.commit_source(pin=True)

    def test_synced_name_is_reserved(self) -> None:
        write_skill(self.source, "engineering", "synced")
        self.commit_source(pin=True)
        self.selection.append("sources/acme/skills/skills/engineering/synced")
        self.write_selection()
        self.commit_skillset()
        self.assert_reconcile_fails(self.run_reconciler(), "reserved by Claude Code")

    def test_existing_consumer_skill_identity_collision_is_detected(self) -> None:
        local = self.home / ".agents/skills/local-folder"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("---\nname: alpha\ndescription: local\n---\n", encoding="utf-8")
        self.assert_reconcile_fails(self.run_reconciler(), "existing consumer skill identity collision")
        self.assertFalse((self.home / ".claude").exists())

    def test_unowned_same_target_symlink_is_not_adopted(self) -> None:
        expected = self.source / "skills/engineering/alpha"
        link = self.home / ".agents/skills/alpha"
        link.parent.mkdir(parents=True)
        link.symlink_to(expected)

        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unowned or changed consumer entry collision")
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
        self.assert_reconcile_fails(result, "unowned or changed consumer entry collision")
        self.assertEqual((collision / "marker").read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.home / ".agents/skills/beta").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_destination_and_parent_symlinks_are_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        agents = self.home / ".agents"
        agents.symlink_to(outside)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed directory is a symlink")
        self.assertEqual(list(outside.iterdir()), [])

        agents.unlink()
        destination = self.home / ".agents/skills"
        destination.parent.mkdir()
        destination.symlink_to(outside)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed directory is a symlink")
        self.assertEqual(list(outside.iterdir()), [])

    def test_blocking_harness_parent_is_rejected_before_other_destination_changes(self) -> None:
        (self.home / ".claude").write_text("preserve", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed parent is not a directory")
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual((self.home / ".claude").read_text(encoding="utf-8"), "preserve")

    def test_stale_link_is_removed_after_selected_source_path_disappears(self) -> None:
        installed = self.run_reconciler()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        stale_target = self.source / "skills/engineering/alpha"
        for harness in ("agents", "claude"):
            self.assertTrue((self.home / f".{harness}/skills/alpha").is_symlink())

        git(["rm", "-r", "skills/engineering/alpha"], self.source)
        self.commit_source(pin=True)
        self.selection = []
        self.write_selection()
        self.commit_skillset("remove selection")
        self.assertFalse(stale_target.exists())

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 2 owned skill links", result.stdout)
        for harness in ("agents", "claude"):
            link = self.home / f".{harness}/skills/alpha"
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
        for harness in ("agents", "claude"):
            self.assertFalse((self.home / f".{harness}/skills/alpha").is_symlink())

    def test_missing_uninitialized_source_fails_without_initializing(self) -> None:
        clone = self.base / "uninitialized"
        git(["clone", "-q", str(self.repo), str(clone)], self.base)
        result = self.run_reconciler(repo=clone)
        self.assert_reconcile_fails(result, "source is missing or uninitialized")
        self.assertFalse((clone / "sources/acme/skills/.git").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_dirty_source_fails_before_consumer_changes(self) -> None:
        (self.source / "skills/engineering/alpha/SKILL.md").write_text("changed", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "source checkout is dirty")
        self.assertFalse((self.home / ".agents").exists())

    def test_wrong_source_revision_fails_before_consumer_changes(self) -> None:
        write_skill(self.source, "misc", "new-commit")
        git(["add", "skills/misc/new-commit"], self.source)
        git(["commit", "-qm", "unrecorded source update"], self.source)
        self.assertNotEqual(git(["rev-parse", "HEAD"], self.source), self.initial_source_commit)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "source revision mismatch")
        self.assertFalse((self.home / ".agents").exists())

    def test_uncommitted_aggregate_configuration_is_rejected(self) -> None:
        (self.repo / "skills.txt").write_text("# uncommitted change\n", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "tracked skillset changes are not committed")
        self.assertFalse((self.home / ".agents").exists())

    def test_staged_gitlink_difference_is_rejected_even_when_worktree_is_at_head_pin(self) -> None:
        write_skill(self.source, "misc", "newer")
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
        write_skill(self.source, "engineering", "shadow")
        self.commit_source(pin=True)
        shadow = self.source / "skills/engineering/shadow"
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "SKILL.md").write_text("---\nname: shadow\ndescription: fixture\n---\n", encoding="utf-8")
        self.selection.append("sources/acme/skills/skills/engineering/shadow")
        self.write_selection()
        self.commit_skillset()
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "selected skill contains untracked or ignored content")
        self.assertFalse((self.home / ".agents").exists())

    def test_relocation_uses_recorded_old_target_to_relink(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        moved = self.base / "moved-skillset"
        git(["clone", "-q", str(self.repo), str(moved)], self.base)
        git(["-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q"], moved)

        result = self.run_reconciler(repo=moved)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = moved / "sources/acme/skills/skills/engineering/alpha"
        self.assert_link("agents", "alpha", expected)
        self.assert_link("claude", "alpha", expected)
        self.assertIn("removed 2 owned skill links and created 2 skill links", result.stdout)

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
        failed = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-consumer-remove"})
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

    def test_historical_commit_reconstructs_its_source_gitlink_and_selection(self) -> None:
        original_selection = (self.repo / "skills.txt").read_text(encoding="utf-8")
        old_skillset_commit = git(["rev-parse", "HEAD"], self.repo)
        old_source_commit = git(["rev-parse", "HEAD"], self.source)
        old_body = (self.source / "skills/engineering/alpha/SKILL.md").read_text()
        self.assertEqual(self.run_reconciler().returncode, 0)

        (self.source / "skills/engineering/alpha/SKILL.md").write_text(old_body + "Version two\n")
        write_skill(self.source, "engineering", "gamma")
        self.commit_source(pin=True)
        self.selection = [self.beta]
        self.write_selection()
        self.commit_skillset("select beta")
        self.assertEqual(self.run_reconciler().returncode, 0)
        self.assert_link("agents", "beta", self.source / "skills/engineering/beta")
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
        self.assert_link("agents", "alpha", historical / "sources/acme/skills/skills/engineering/alpha")
        self.assert_link("claude", "alpha", historical / "sources/acme/skills/skills/engineering/alpha")
        self.assertFalse((self.home / ".agents/skills/beta").exists())
        self.assertEqual(self.run_reconciler("--check", repo=historical).returncode, 0)
        self.assertEqual((self.home / ".agents/skills/alpha/SKILL.md").read_text(), old_body)


    def test_multiline_description_and_nested_metadata_preserve_plain_identity(self) -> None:
        file = self.source / "skills/engineering/alpha/SKILL.md"
        file.write_text("---\n# comment\nname: alpha\ndescription: |\n  Uses a filename and a name.\n  name: text, not a field\nmetadata:\n  author: fixture\n---\nbody\n")
        self.commit_source()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_escaped_first_character_of_duplicate_identity_is_rejected(self) -> None:
        file = self.source / "skills/engineering/alpha/SKILL.md"
        file.write_text('---\nname: alpha\n"\\u006eame": beta\ndescription: fixture\n---\n')
        self.commit_source()
        self.assert_reconcile_fails(self.run_reconciler(), "unsupported frontmatter")
        self.assertFalse((self.home / ".agents").exists())

    def test_symlinked_unrelated_skill_identity_collision_is_detected(self) -> None:
        other = self.base / "unrelated-skill"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: alpha\ndescription: unrelated\n---\n")
        alias = self.home / ".agents/skills/local-folder"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(other)
        self.assert_reconcile_fails(self.run_reconciler(), "existing consumer skill identity collision")
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

    def test_missing_receipt_never_reclaims_same_target_consumer(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        entry = self.home / ".agents/skills/alpha"
        before = entry.lstat()
        (self.home / ".agents/.skillset/receipts/alpha").unlink()
        self.assert_reconcile_fails(self.run_reconciler(), "unowned or changed consumer entry collision")
        self.assertTrue(os.path.samestat(before, entry.lstat()))

    def test_corrupt_receipt_is_rejected_without_removing_consumer(self) -> None:
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
        self.assert_reconcile_fails(self.run_reconciler(), "unowned or changed consumer entry collision")
        self.selection = []
        self.write_selection()
        self.commit_skillset()
        self.assert_reconcile_fails(self.run_reconciler(), "owned consumer entry changed")
        self.assertTrue(os.path.samestat(before, entry.lstat()))

    def test_interrupted_receipt_can_switch_to_a_new_target_in_one_run(self) -> None:
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-receipt-create"})
        self.assertEqual(result.returncode, 73, result.stderr)
        moved = self.source / "skills/productivity/alpha"
        moved.parent.mkdir()
        (self.source / "skills/engineering/alpha").rename(moved)
        self.commit_source()
        self.selection = ["sources/acme/skills/skills/productivity/alpha"]
        self.write_selection()
        self.commit_skillset()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link("agents", "alpha", moved)
        self.assertEqual(self.run_reconciler("--check").returncode, 0)

    def test_interruption_after_publication_keeps_exact_ownership(self) -> None:
        result = self.run_reconciler(extra_env={"SKILLSET_TEST_FAIL_AT": "after-consumer-publish"})
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


    def test_single_skill_at_source_root_needs_no_layout_adapter(self) -> None:
        shutil.rmtree(self.source / "skills")
        (self.source / "SKILL.md").write_text("---\nname: skills\ndescription: root skill\n---\n")
        self.commit_source()
        self.selection = ["sources/acme/skills"]
        self.write_selection()
        self.commit_skillset()
        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link("agents", "skills", self.source)

    def test_selected_directory_cannot_implicitly_expose_nested_skills(self) -> None:
        nested = self.source / "skills/engineering/alpha/nested"
        nested.mkdir()
        (nested / "SKILL.md").write_text("---\nname: nested\ndescription: unselected\n---\n")
        self.commit_source()
        self.assert_reconcile_fails(self.run_reconciler(), "additional SKILL.md")
        self.assertFalse((self.home / ".agents").exists())

    def test_directory_symlink_cannot_smuggle_unselected_skills(self) -> None:
        (self.source / "skills/engineering/alpha/linked").symlink_to("../beta")
        self.commit_source()
        self.assert_reconcile_fails(self.run_reconciler(), "directory symlinks")
        self.assertFalse((self.home / ".agents").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
