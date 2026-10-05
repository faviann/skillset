"""The shared fixture supports shipped files and later local source updates."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from fixture import CHECKOUT, SkillsetFixture, git


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="skillset-fixture-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.selection = ["acme/skills:alpha", "other/beta:beta"]
        self.paths = ["sources/acme/skills/nested/alpha", "sources/other/beta"]
        self.fixture = SkillsetFixture(self.base, {
            "acme/skills": {"nested/alpha": "alpha", "unselected": "unselected"},
            "other/beta": {".": "beta"},
        }, self.selection)

    def test_builds_committed_sources_selection_and_shipped_files(self) -> None:
        repo = self.fixture.repo
        shipped = set(git(
            ["ls-files", "-z", "--", "scripts", "setup.sh", ".gitignore"], CHECKOUT,
        ).split("\0")) - {""}
        self.assertEqual(set(git(["ls-files", "-z"], repo).split("\0")) - {""}, shipped | {
            ".gitmodules", "skills.txt", "sources.toml", "sources/acme/skills", "sources/other/beta",
        })
        for name in shipped:
            with self.subTest(file=name):
                self.assertEqual((repo / name).read_bytes(), (CHECKOUT / name).read_bytes())
                self.assertEqual((repo / name).stat().st_mode, (CHECKOUT / name).stat().st_mode)
        self.assertEqual((repo / "skills.txt").read_text(),
                         "# fixture selection\n" + "\n".join(self.selection) + "\n")
        self.assertEqual((repo / "sources.toml").read_text(), "")
        for path, name in zip(self.paths, ("alpha", "beta")):
            with self.subTest(skill=path):
                self.assertIn(f"name: {name}\n", (repo / path / "SKILL.md").read_text())
        self.assertTrue((repo / "sources/acme/skills/unselected/SKILL.md").is_file())
        self.assertEqual(git(["status", "--porcelain"], repo), "")

    def test_later_submodule_update_inherits_file_protocol_permission(self) -> None:
        repo = self.fixture.repo
        self.assertEqual(git(["config", "--local", "--get", "protocol.file.allow"],
                             repo, ok=False), "")
        clone = self.base / "later-clone"
        for args in (
            ["clone", "-q", str(repo), str(clone)],
            ["-C", str(clone), "submodule", "update", "--init", "--checkout", "-q"],
        ):
            result = subprocess.run(
                ["git", *args], cwd=self.base, env=self.fixture.env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.paths:
            self.assertEqual((clone / path / "SKILL.md").read_bytes(),
                             (repo / path / "SKILL.md").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
