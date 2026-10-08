"""The update command refuses, syncs the live checkout with origin, reports each source and finishes."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fixture import SkillsetFixture, configure_git, git


class UpdateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-update-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = SkillsetFixture(self.base, {
            "acme/skills": {"skills/alpha": "alpha", "skills/unselected": "unselected"},
            "zebra/tools": {"gamma": "gamma"},
        }, ["acme/skills:alpha"])
        self.repo = self.fixture.repo
        self.home = self.fixture.home
        self.env = self.fixture.env
        # The command strips the fixture's GIT_CONFIG_* variables, so the
        # worktree's source clones need the file protocol allowed in a config file.
        with (self.base / "config/git/config").open("a", encoding="utf-8") as config:
            config.write('[protocol "file"]\n\tallow = always\n')
        self.origin = self.base / "origin.git"
        git(["init", "-q", "--bare", "-b", "main", str(self.origin)], self.base)
        git(["branch", "-M", "main"], self.repo)
        git(["remote", "add", "origin", str(self.origin)], self.repo)
        git(["push", "-q", "-u", "origin", "main"], self.repo)
        self.worktree = self.home / "worktrees/skillset/update-sources"

    def update(self, answers: str = "", *, ok: bool = True, script: Path | None = None) -> str:
        script = script or self.repo / "scripts/update-sources.sh"
        result = subprocess.run([str(script)], cwd=self.repo, env=self.env, input=answers, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, check=False)
        self.assertEqual(result.returncode == 0, ok, result.stdout)
        return result.stdout

    def check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.repo / "scripts/reconcile-skills.sh"), "--check"], cwd=self.repo,
                              env=self.env, text=True, capture_output=True, timeout=60, check=False)

    def advance(self, source: str, message: str = "advance") -> str:
        """Commit on the source's tracked branch at its origin and return the new tip."""
        origin = self.fixture.origins[source]
        git(["commit", "--allow-empty", "-qm", message], origin)
        return git(["rev-parse", "HEAD"], origin)

    def publish_pin(self, source: str, tip: str) -> None:
        """Bump the source's pin on origin from the live checkout, then undo the live checkout's part."""
        checkout = self.fixture.sources[source]
        git(["fetch", "-q", "origin"], checkout)
        git(["checkout", "-q", "--detach", tip], checkout)
        git(["commit", "-qm", f"Bump {source} elsewhere", "--", f"sources/{source}"], self.repo)
        git(["push", "-q", "origin", "main"], self.repo)
        git(["reset", "-q", "--hard", "HEAD~1"], self.repo)
        git(["submodule", "update", "-q", "--checkout"], self.repo)

    def origin_subjects(self) -> list[str]:
        return git(["log", "--format=%s", "main"], self.origin).splitlines()

    def pin(self, source: str, *, repo: Path | None = None, ref: str = "HEAD") -> str:
        return git(["rev-parse", f"{ref}:sources/{source}"], repo or self.repo)

    def add_canonical(self, source: str) -> Path:
        """Declare the source a fork: its canonical repository lives at the redirected GitHub URL."""
        canonical = self.fixture.github / f"{source}.git"
        git(["clone", "-q", str(self.fixture.origins[source]), str(canonical)], self.base)
        configure_git(canonical)
        upstream = git(["rev-parse", "HEAD"], canonical)
        (self.repo / "sources.toml").write_text(f'["{source}"]\nupstream = "{upstream}"\n', encoding="utf-8")
        git(["commit", "-qam", "Declare the fork"], self.repo)
        git(["push", "-q", "origin", "main"], self.repo)
        return canonical


class UpdateStartTests(UpdateCase):
    def assert_refused(self, reason: str, script: Path | None = None) -> None:
        self.assertIn(reason, self.update(ok=False, script=script))
        self.assertFalse(self.worktree.exists())
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual(self.origin_subjects(), ["initial skillset"])

    def test_refuses_off_main_with_unsaved_selection_or_blocked_install(self) -> None:
        git(["checkout", "-q", "-b", "topic"], self.repo)
        self.assert_refused("is on topic, not main")
        git(["checkout", "-q", "main"], self.repo)
        (self.repo / "skills.txt").write_text("# unsaved\nacme/skills:alpha\n", encoding="utf-8")
        self.assert_refused("skills.txt has uncommitted changes")
        git(["checkout", "-q", "--", "skills.txt"], self.repo)
        linked = self.base / "linked"
        git(["worktree", "add", "-q", str(linked)], self.repo)
        self.assert_refused("belong to the primary checkout", linked / "scripts/update-sources.sh")

    def test_rerun_restores_sources_an_interrupted_finish_left_behind(self) -> None:
        self.update()
        checkout = self.fixture.sources["acme/skills"]
        git(["commit", "--allow-empty", "-qm", "moved by hand"], checkout)
        output = self.update()
        self.assertIn("acme/skills: up to date\nzebra/tools: up to date\n", output)
        self.assertEqual(git(["rev-parse", "HEAD"], checkout), self.pin("acme/skills"))
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")


class UpdateSyncTests(UpdateCase):
    def test_up_to_date_run_merges_origin_main_moves_sources_and_installs(self) -> None:
        tip = self.advance("zebra/tools")
        self.publish_pin("zebra/tools", tip)
        self.assertNotEqual(self.pin("zebra/tools"), tip)
        output = self.update()
        self.assertIn("acme/skills: up to date\nzebra/tools: up to date\n", output)
        self.assertIn("created 2 skill links", output)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["zebra/tools"]), tip)
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(git(["symbolic-ref", "--short", "HEAD"], self.worktree), "update-sources")
        self.assertEqual(git(["rev-parse", "HEAD"], self.worktree), git(["rev-parse", "HEAD"], self.repo))

    def test_rerun_recovers_a_conflicted_worktree_with_dirty_sources(self) -> None:
        self.update()
        (self.worktree / "skills.txt").write_text("# worktree\nacme/skills:alpha\n", encoding="utf-8")
        git(["commit", "-qam", "worktree selection"], self.worktree)
        other = self.base / "other"
        git(["clone", "-q", str(self.origin), str(other)], self.base)
        configure_git(other)
        (other / "skills.txt").write_text("# other machine\nacme/skills:alpha\n", encoding="utf-8")
        git(["commit", "-qam", "other selection"], other)
        git(["push", "-q", "origin", "main"], other)
        git(["fetch", "-q", "origin"], self.worktree)
        git(["merge", "-q", "origin/main"], self.worktree, ok=False)
        self.assertTrue((self.worktree / ".git").exists())
        self.assertTrue(git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], self.worktree))
        source = self.worktree / "sources/acme/skills"
        (source / "junk.txt").write_text("junk\n", encoding="utf-8")
        (source / "skills/alpha/SKILL.md").write_text("broken\n", encoding="utf-8")
        output = self.update()
        self.assertIn("acme/skills: up to date", output)
        self.assertEqual(git(["status", "--porcelain", "--ignore-submodules=none"], self.worktree), "")
        self.assertFalse(git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], self.worktree, ok=False))
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertIn("# other machine", (self.repo / "skills.txt").read_text(encoding="utf-8"))

    def test_fork_behind_upstream_is_reported_and_left_alone(self) -> None:
        canonical = self.add_canonical("acme/skills")
        git(["commit", "--allow-empty", "-qm", "upstream change"], canonical)
        fork_refs = git(["for-each-ref"], self.fixture.origins["acme/skills"])
        before = self.origin_subjects()
        output = self.update()
        self.assertIn("acme/skills: fork behind upstream", output)
        self.assertEqual(self.origin_subjects(), before)
        self.assertEqual(git(["for-each-ref"], self.fixture.origins["acme/skills"]), fork_refs)
        self.assertEqual(git(["status", "--porcelain"], self.repo), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
