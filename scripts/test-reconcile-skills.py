#!/usr/bin/env python3
"""Git/filesystem integration tests for the committed skillset reconciler."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
        return subprocess.run(
            [str(checkout / "scripts/reconcile-skills.sh"), *args],
            cwd=checkout,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

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
        self.assert_reconcile_fails(result, "use plain `name: skill-name`")
        self.assertFalse((self.home / ".agents").exists())

    def test_duplicate_frontmatter_identity_fails_closed(self) -> None:
        skill_file = self.source / "skills/engineering/alpha/SKILL.md"
        skill_file.write_text("---\nname: alpha\nname: beta\n---\n", encoding="utf-8")
        self.commit_source(pin=True)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "expected one plain name field")

    def test_unowned_same_target_symlink_is_not_adopted(self) -> None:
        expected = self.source / "skills/engineering/alpha"
        link = self.home / ".agents/skills/alpha"
        link.parent.mkdir(parents=True)
        link.symlink_to(expected)

        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unowned symlink collision (same target)")
        self.assertEqual(os.readlink(link), str(expected))
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".local").exists())

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
        self.assert_reconcile_fails(result, "unowned entry collision")
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
        self.assert_reconcile_fails(result, "managed destination is not a directory")
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

    def test_changed_link_during_pending_update_fails_closed(self) -> None:
        self.assertEqual(self.run_reconciler().returncode, 0)
        state_path = self.home / ".local/state/skillset/ownership.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["phase"] = "pending"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        link = self.home / ".agents/skills/alpha"
        link.unlink()
        replacement = self.base / "replacement"
        replacement.mkdir()
        link.symlink_to(replacement)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "unowned or changed symlink collision")
        self.assertEqual(os.readlink(link), str(replacement))
        self.assertTrue((self.home / ".claude/skills/alpha").is_symlink())
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["phase"], "pending")

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

    def test_pending_ownership_record_recovers_interrupted_apply(self) -> None:
        target = str(self.source / "skills/engineering/alpha")
        records = [
            {"harness": harness, "name": "alpha", "target": target}
            for harness in ("agents", "claude")
        ]
        state_dir = self.home / ".local/state/skillset"
        state_dir.mkdir(parents=True)
        state = {"version": 1, "phase": "pending", "links": records}
        (state_dir / "ownership.json").write_text(json.dumps(state), encoding="utf-8")
        agents_link = self.home / ".agents/skills/alpha"
        agents_link.parent.mkdir(parents=True)
        agents_link.symlink_to(target)

        result = self.run_reconciler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_link("agents", "alpha", Path(target))
        self.assert_link("claude", "alpha", Path(target))
        final = json.loads((state_dir / "ownership.json").read_text(encoding="utf-8"))
        self.assertEqual(final["phase"], "complete")

    def test_corrupt_ownership_state_fails_without_touching_links(self) -> None:
        state_dir = self.home / ".local/state/skillset"
        state_dir.mkdir(parents=True)
        (state_dir / "ownership.json").write_text("not json", encoding="utf-8")
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "ownership state is unreadable or invalid")
        self.assertFalse((self.home / ".agents").exists())

    def test_structurally_ambiguous_ownership_state_fails_closed(self) -> None:
        state_dir = self.home / ".local/state/skillset"
        state_dir.mkdir(parents=True)
        (state_dir / "ownership.json").write_text(
            '{"version": 1, "phase": [], "links": []}', encoding="utf-8"
        )
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "invalid ownership state structure")
        self.assertFalse((self.home / ".agents").exists())

    def test_symlinked_state_parent_is_rejected(self) -> None:
        outside = self.base / "outside-state"
        outside.mkdir()
        (self.home / ".local").symlink_to(outside)
        result = self.run_reconciler()
        self.assert_reconcile_fails(result, "managed directory is a symlink")
        self.assertEqual(list(outside.iterdir()), [])

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

        write_skill(self.source, "engineering", "gamma")
        self.commit_source(pin=True)
        self.assertNotEqual(git(["rev-parse", "HEAD"], self.source), old_source_commit)

        historical = self.base / "historical"
        git(["clone", "-q", str(self.repo), str(historical)], self.base)
        git(["checkout", "-q", old_skillset_commit], historical)
        git(
            ["-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive", "-q"],
            historical,
        )
        self.assertEqual(
            git(["rev-parse", "HEAD"], historical / "sources/acme/skills"),
            old_source_commit,
        )
        self.assertEqual(
            (historical / "skills.txt").read_text(encoding="utf-8"), original_selection
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
