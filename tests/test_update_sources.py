"""The update command bumps sources one at a time and publishes only what you accept."""

from __future__ import annotations

import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from fixture import SkillsetFixture, configure_git, git, write_skill


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
        origin = self.fixture.origins[source]
        git(["commit", "--allow-empty", "-qm", message], origin)
        return git(["rev-parse", "HEAD"], origin)

    def push_pin_elsewhere(self, source: str, tip: str) -> None:
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

    def push_elsewhere(self, selection: str, subject: str) -> None:
        other = self.base / "other"
        git(["clone", "-q", str(self.origin), str(other)], self.base)
        configure_git(other)
        (other / "skills.txt").write_text(selection, encoding="utf-8")
        git(["commit", "-qam", subject], other)
        git(["push", "-q", "origin", "main"], other)

    def add_canonical(self, source: str) -> Path:
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
        old, checkout = self.pin("acme/skills"), self.fixture.sources["acme/skills"]
        git(["checkout", "-q", "-b", "topic"], self.repo)
        tip = self.advance("acme/skills")
        git(["fetch", "-q", "origin"], checkout)
        git(["checkout", "-q", "--detach", tip], checkout)
        git(["commit", "-qm", "topic pin", "--", "sources/acme/skills"], self.repo)
        git(["checkout", "-q", "--detach", old], checkout)
        self.assert_refused("is on topic, not main")
        self.assertEqual(git(["rev-parse", "HEAD"], checkout), old)
        git(["checkout", "-q", "main"], self.repo)
        (self.repo / "skills.txt").write_text("# unsaved\nacme/skills:alpha\n", encoding="utf-8")
        self.assert_refused("skills.txt has uncommitted changes")
        git(["checkout", "-q", "--", "skills.txt"], self.repo)
        (self.repo / "sources.toml").write_text("# dirty\n", encoding="utf-8")
        self.assert_refused("tracked skillset changes are not committed")
        git(["checkout", "-q", "--", "sources.toml"], self.repo)
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
        self.push_pin_elsewhere("zebra/tools", tip)
        self.assertNotEqual(self.pin("zebra/tools"), tip)
        output = self.update()
        self.assertIn("acme/skills: up to date\nzebra/tools: up to date\n", output)
        self.assertIn("created 2 skill links", output)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["zebra/tools"]), tip)
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(git(["symbolic-ref", "--short", "HEAD"], self.worktree), "update-sources")
        self.assertEqual(git(["rev-parse", "HEAD"], self.worktree), git(["rev-parse", "HEAD"], self.repo))

    def test_a_source_url_moved_on_origin_main_is_followed_in_one_run(self) -> None:
        moved = self.base / "moved.git"
        git(["clone", "-q", "--bare", str(self.fixture.origins["acme/skills"]), str(moved)], self.base)
        scratch = self.base / "scratch"
        git(["clone", "-q", str(moved), str(scratch)], self.base)
        configure_git(scratch)
        git(["commit", "-q", "--allow-empty", "-m", "only at the new URL"], scratch)
        git(["push", "-q", "origin", "main"], scratch)
        tip = git(["rev-parse", "HEAD"], scratch)
        other = self.base / "other"
        git(["clone", "-q", str(self.origin), str(other)], self.base)
        configure_git(other)
        git(["config", "-f", ".gitmodules", "submodule.sources/acme/skills.url", str(moved)], other)
        git(["update-index", "--cacheinfo", f"160000,{tip},sources/acme/skills"], other)
        git(["commit", "-qam", "Move acme/skills to a new URL"], other)
        git(["push", "-q", "origin", "main"], other)
        output = self.update()
        self.assertIn("acme/skills: up to date", output)
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["acme/skills"]), tip)
        self.assertEqual(git(["config", "--get", "submodule.sources/acme/skills.url"], self.repo), str(moved))
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertEqual(self.check().returncode, 0)

    def test_rerun_recovers_a_conflicted_worktree_with_dirty_sources(self) -> None:
        self.update()
        (self.worktree / "skills.txt").write_text("# worktree\nacme/skills:alpha\n", encoding="utf-8")
        git(["commit", "-qam", "worktree selection"], self.worktree)
        self.push_elsewhere("# other machine\nacme/skills:alpha\n", "other selection")
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

    def test_a_synced_fork_moves_its_pin_and_upstream_to_the_merge_base(self) -> None:
        canonical = self.add_canonical("acme/skills")
        fork = self.fixture.origins["acme/skills"]
        git(["commit", "-qam", "fork change", "--allow-empty"], fork)
        write_skill(canonical, "skills/beta")
        git(["add", "-A"], canonical)
        git(["commit", "-qm", "upstream change"], canonical)
        git(["pull", "-q", "--no-rebase", "--no-edit", str(canonical), "main"], fork)
        tip, canonical_tip = git(["rev-parse", "HEAD"], fork), git(["rev-parse", "HEAD"], canonical)
        git(["commit", "-q", "--allow-empty", "-m", "after the sync"], canonical)
        refs = {repo: git(["for-each-ref"], repo) for repo in (fork, canonical)}
        output = self.update("a\n")
        self.assertIn("acme/skills: pushed", output)
        self.assertEqual(self.origin_subjects()[0], f"Update acme/skills to {tip[:7]}")
        self.assertEqual(git(["show", "--format=", "--name-only", "main"], self.origin),
                         "sources.toml\nsources/acme/skills")
        self.assertEqual((self.repo / "sources.toml").read_text(encoding="utf-8"),
                         f'["acme/skills"]\nupstream = "{canonical_tip}"\n')
        self.assertEqual({repo: git(["for-each-ref"], repo) for repo in (fork, canonical)}, refs)
        self.assertEqual(self.check().returncode, 0)


class UpdateApplyTests(UpdateCase):
    def start(self) -> subprocess.Popen[str]:
        return subprocess.Popen([str(self.repo / "scripts/update-sources.sh")], cwd=self.repo, env=self.env,
                                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def read_until(self, process: subprocess.Popen[str], marker: str) -> str:
        output = ""
        while marker not in output:
            char = process.stdout.read(1)
            self.assertTrue(char, f"the command ended before {marker!r}:\n{output}")
            output += char
        return output

    def answer(self, process: subprocess.Popen[str], text: str, *, ok: bool = True) -> str:
        output, _ = process.communicate(text, timeout=180)
        self.assertEqual(process.returncode == 0, ok, output)
        return output

    def test_each_accepted_source_is_its_own_commit_on_main_and_the_live_checkout_follows(self) -> None:
        write_skill(self.fixture.origins["acme/skills"], "skills/beta")
        git(["add", "-A"], self.fixture.origins["acme/skills"])
        old = self.pin("acme/skills")
        acme = self.advance("acme/skills", "add beta")
        zebra = self.advance("zebra/tools")
        output = self.update("a\na\n")
        self.assertIn(f"acme/skills {old[:7]} -> {acme[:7]}, 1 commit\n  added: beta\n", output)
        self.assertEqual(self.origin_subjects(), [
            f"Update zebra/tools to {zebra[:7]}", f"Update acme/skills to {acme[:7]}", "initial skillset",
        ])
        self.assertEqual(git(["show", "--format=", "--name-only", "main"], self.origin), "sources/zebra/tools")
        self.assertEqual(git(["show", "--format=", "--name-only", "main~1"], self.origin), "sources/acme/skills")
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        for source, tip in (("acme/skills", acme), ("zebra/tools", zebra)):
            self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources[source]), tip)
        self.assertEqual(self.check().returncode, 0)

    def test_summary_flags_a_changed_selected_skill_and_its_diff_covers_the_directory(self) -> None:
        origin = self.fixture.origins["acme/skills"]
        with (origin / "skills/alpha/SKILL.md").open("a", encoding="utf-8") as skill:
            skill.write("\nRewritten instructions.\n")
        (origin / "skills/alpha/helper.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        with (origin / "skills/unselected/SKILL.md").open("a", encoding="utf-8") as skill:
            skill.write("\nAlso changed.\n")
        git(["add", "-A"], origin)
        self.advance("acme/skills", "rewrite")
        head, old = git(["rev-parse", "HEAD"], self.repo), self.pin("acme/skills")
        output = self.update("d\ns\n")
        summary, diff = output.split("[a]ccept", 1)
        self.assertRegex(summary, r"changed: .*\balpha\*")
        self.assertRegex(summary, r"changed: .*\bunselected(?!\*)")
        self.assertIn("skills/alpha/SKILL.md", diff)
        self.assertIn("skills/alpha/helper.sh", diff)
        self.assertNotIn("skills/unselected", diff)
        self.assertIn("acme/skills: skipped\n", output)
        self.assertEqual(self.origin_subjects(), ["initial skillset"])
        self.assertEqual((git(["rev-parse", "HEAD"], self.repo), self.pin("acme/skills")), (head, old))
        self.assertEqual(git(["rev-parse", "HEAD"], self.worktree), head)
        self.assertEqual(git(["rev-parse", "HEAD"], self.worktree / "sources/acme/skills"), old)

    def test_a_failed_source_leaves_the_others_processed_and_its_pin_unchanged(self) -> None:
        self.fixture.add_source("mid/tools", {"delta": "delta"})
        git(["commit", "-qm", "Add mid/tools"], self.repo)
        git(["push", "-q", "origin", "main"], self.repo)
        self.update()
        shutil.rmtree(self.fixture.origins["zebra/tools"])
        acme, mid = self.advance("acme/skills"), self.advance("mid/tools")
        output = self.update("a\na\n", ok=False)
        self.assertIn("zebra/tools: skipped: git fetch", output)
        self.assertEqual(self.origin_subjects()[:2],
                         [f"Update mid/tools to {mid[:7]}", f"Update acme/skills to {acme[:7]}"])
        self.assertEqual(self.pin("zebra/tools", repo=self.origin, ref="main"), self.pin("zebra/tools"))
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertEqual(self.check().returncode, 0)

    def test_an_unresolved_selection_skips_the_source_and_names_what_the_update_adds(self) -> None:
        origin = self.fixture.origins["acme/skills"]
        git(["mv", "skills/alpha", "skills/omega"], origin)
        write_skill(origin, "skills/omega")
        git(["add", "-A"], origin)
        self.advance("acme/skills", "rename alpha")
        output = self.update(ok=False)
        self.assertIn("acme/skills: skipped: skills.txt:2: name is missing at the pinned commit: acme/skills:alpha",
                      output)
        self.assertIn("the update adds: omega", output)
        self.assertEqual(self.origin_subjects(), ["initial skillset"])
        self.assertEqual(self.check().returncode, 0)

    def test_main_that_moves_during_the_run_is_merged_validated_and_pushed(self) -> None:
        acme = self.advance("acme/skills")
        process = self.start()
        self.read_until(process, "[a]ccept")
        self.push_elsewhere("# other machine\nacme/skills:alpha\n", "other selection")
        output = self.answer(process, "a\n")
        self.assertIn("acme/skills: pushed", output)
        self.assertEqual(sorted(self.origin_subjects())[1:], sorted([
            f"Update acme/skills to {acme[:7]}", "initial skillset", "other selection",
        ]))
        self.assertEqual(len(git(["log", "--format=%P", "-1", "main"], self.origin).split()), 2)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertIn("# other machine", (self.repo / "skills.txt").read_text(encoding="utf-8"))
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["acme/skills"]), acme)
        self.assertEqual(self.check().returncode, 0)

    def test_a_moved_main_whose_selection_the_update_breaks_is_not_published(self) -> None:
        git(["rm", "-rq", "skills/unselected"], self.fixture.origins["acme/skills"])
        self.advance("acme/skills", "remove unselected")
        head = git(["rev-parse", "HEAD"], self.repo)
        process = self.start()
        self.read_until(process, "[a]ccept")
        self.push_elsewhere("acme/skills:alpha\nacme/skills:unselected\n", "select unselected elsewhere")
        output = self.answer(process, "a\n", ok=False)
        self.assertIn("acme/skills: skipped: skills.txt:2: name is missing at the pinned commit: "
                      "acme/skills:unselected", output)
        self.assertEqual(self.origin_subjects(), ["select unselected elsewhere", "initial skillset"])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head)
        self.assertEqual(self.check().returncode, 0)

    def test_a_rejected_push_skips_the_source_and_the_next_one_starts_from_a_clean_base(self) -> None:
        hook = self.origin / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nwhile read old new ref; do\n"
                        "  git log --format=%s \"$old..$new\" | grep -q '^Update acme/skills ' && exit 1\n"
                        "done\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        acme, zebra = self.advance("acme/skills"), self.advance("zebra/tools")
        output = self.update("a\na\n", ok=False)
        self.assertIn("acme/skills: skipped: git push", output)
        self.assertIn("zebra/tools: pushed", output)
        self.assertEqual(self.origin_subjects(), [f"Update zebra/tools to {zebra[:7]}", "initial skillset"])
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))
        self.assertNotEqual(self.pin("acme/skills"), acme)
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["acme/skills"]), self.pin("acme/skills"))
        self.assertEqual(self.check().returncode, 0)

    def test_a_local_selection_commit_is_announced_and_published_with_the_first_accepted_source(self) -> None:
        (self.repo / "skills.txt").write_text("# local\nacme/skills:alpha\n", encoding="utf-8")
        git(["commit", "-qam", "Update skill selection (+0 -0)"], self.repo)
        local = git(["rev-parse", "HEAD"], self.repo)
        self.advance("acme/skills")
        self.advance("zebra/tools")
        output = self.update("a\ns\n")
        first, second = output.split("[a]ccept")[:2]
        self.assertIn(f"also publishes 1 local commit(s) with this push:\n    {local[:7]} Update skill selection (+0 -0)\n",
                      first)
        self.assertNotIn("also publishes", second)
        self.assertEqual(git(["rev-parse", "main~1"], self.origin), local)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), git(["rev-parse", "main"], self.origin))

    def test_interrupting_at_the_prompt_says_how_to_finish(self) -> None:
        self.advance("acme/skills")
        process = self.start()
        self.read_until(process, "[a]ccept")
        process.send_signal(signal.SIGINT)
        output = self.answer(process, "", ok=False)
        self.assertEqual(process.returncode, 130)
        self.assertIn("interrupted; accepted sources are already on origin/main; "
                      "rerun scripts/update-sources.sh to finish", output)
        self.assertNotIn("Traceback", output)

    def test_a_rerun_finishes_when_the_live_checkout_moved_after_the_push(self) -> None:
        acme = self.advance("acme/skills")
        process = self.start()
        self.read_until(process, "[a]ccept")
        git(["commit", "-q", "--allow-empty", "-m", "live change"], self.repo)
        live = git(["rev-parse", "HEAD"], self.repo)
        output = self.answer(process, "a\n", ok=False)
        self.assertIn("acme/skills: pushed", output)
        self.assertIn("cannot fast-forward", output)
        self.assertIn("a rerun finishes", output)
        self.assertEqual(self.pin("acme/skills", repo=self.origin, ref="main"), acme)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), live)
        output = self.update()
        self.assertIn("acme/skills: up to date", output)
        self.assertEqual(self.pin("acme/skills"), acme)
        self.assertEqual(git(["rev-parse", "HEAD"], self.fixture.sources["acme/skills"]), acme)
        git(["merge-base", "--is-ancestor", live, "HEAD"], self.repo)
        self.assertEqual(self.check().returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
