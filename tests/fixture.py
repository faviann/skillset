"""Real Git checkouts shared by the test groups."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


CHECKOUT = Path(__file__).resolve().parent.parent

# A commit spawns a detached `git maintenance run --auto` that can still be
# writing under .git while a test's temporary directory is being removed.
QUIET_GIT = {"maintenance.auto": "false", "gc.auto": "0"}


def git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    # Submodule helpers inherit these, including later updates from a clone.
    config = {"protocol.file.allow": "always", **QUIET_GIT}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def git(args: list[str], cwd: Path, *, ok: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=git_environment(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if ok and result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def configure_git(repo: Path) -> None:
    git(["config", "user.name", "Skillset integration tests"], repo)
    git(["config", "user.email", "skillset-tests@example.invalid"], repo)
    # The selector commits with the test process's environment, not git_environment().
    for key, value in QUIET_GIT.items():
        git(["config", key, value], repo)


def redirect_github(xdg_config_home: Path, directory: Path) -> None:
    config = xdg_config_home / "git/config"
    config.parent.mkdir(parents=True)
    config.write_text(f'[url "{directory}/"]\n\tinsteadOf = https://github.com/\n', encoding="utf-8")


def write_skill(repo: Path, path: str, *, identity: str | None = None) -> Path:
    skill_dir = repo / path
    skill_dir.mkdir(parents=True, exist_ok=True)
    name = identity or skill_dir.name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: fixture\n---\n\n# {skill_dir.name}\n",
        encoding="utf-8",
    )
    return skill_dir


def write_selection(repo: Path, selection: list[str]) -> None:
    content = "# fixture selection\n" + "".join(f"{line}\n" for line in selection)
    (repo / "skills.txt").write_text(content, encoding="utf-8")


class SkillsetFixture:
    """Build a committed skillset with sources keyed by owner/repo.

    Each source maps source-relative skill paths to frontmatter names; selection
    contains owner/repo:name lines independently of those paths. Pass
    ``env`` to subprocesses so local source URLs remain usable by Git helpers.
    The caller owns the temporary base directory and its cleanup.
    """

    def __init__(
        self, base: Path, sources: dict[str, dict[str, str]], selection: list[str],
        *, sources_toml: str = "",
    ) -> None:
        self.base = base
        self.home = base / "home"
        self.home.mkdir()
        self.github = base / "github"
        redirect_github(base / "config", self.github)
        self.env = git_environment()
        self.env["HOME"] = str(self.home)
        self.env["XDG_CONFIG_HOME"] = str(base / "config")
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.repo = base / "skillset"
        self.repo.mkdir()
        git(["init", "-q"], self.repo)
        configure_git(self.repo)
        shipped = git(["ls-files", "-z", "--", "scripts", "setup.sh", ".gitignore"], CHECKOUT)
        for name in filter(None, shipped.split("\0")):
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CHECKOUT / name, destination)
        self.origins: dict[str, Path] = {}
        self.sources: dict[str, Path] = {}
        for name, skills in sources.items():
            self.add_source(name, skills)
        write_selection(self.repo, selection)
        (self.repo / "sources.toml").write_text(sources_toml, encoding="utf-8")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "initial skillset"], self.repo)

    def add_source(self, name: str, skills: dict[str, str]) -> None:
        origin = self.base / "source-origins" / name
        origin.mkdir(parents=True)
        git(["init", "-q", "-b", "main"], origin)
        configure_git(origin)
        for path, identity in skills.items():
            write_skill(origin, path, identity=identity)
        git(["add", "-A"], origin)
        git(["commit", "--allow-empty", "-qm", "initial source"], origin)
        path = f"sources/{name}"
        git(["submodule", "add", "-q", "-b", "main", str(origin), path], self.repo)
        source = self.repo / path
        configure_git(source)
        self.origins[name] = origin
        self.sources[name] = source
