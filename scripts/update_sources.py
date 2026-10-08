#!/usr/bin/env python3
"""Update sources one at a time in a kept worktree and publish each accepted pin to main."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from reconcile_skills import home_path, require_primary_checkout, selection_install_blocker
from skill_catalog import (
    ReconcileError,
    SourceCatalog,
    git,
    git_env,
    load_catalog,
    parse_modules,
    selection_uncommitted,
    update_marker,
)

BRANCH = "update-sources"


def run_git(args: list[str], cwd: Path, *, interactive: bool = False) -> None:
    """Run Git on the terminal; interactive also lets hooks, signing and credential prompts work."""
    if subprocess.run(["git", *args], cwd=cwd, env=git_env(interactive=interactive), check=False).returncode:
        raise ReconcileError(f"git {' '.join(args)} failed")


def update_blocker(root: Path) -> str | None:
    """Why the update cannot start, or None. Read-only, so the selector can show the reason."""
    if reason := selection_install_blocker(root):
        return reason
    branch = git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=root, check=False)
    if branch != "main":
        return f"the live checkout is on {branch or 'a detached HEAD'}, not main"
    if selection_uncommitted(root):
        return "skills.txt has uncommitted changes; save and install first"
    return None


def restore_sources(root: Path, *, force: bool = False) -> None:
    """Put every source at its pin, as ./setup.sh does; force also discards changes inside them."""
    args = ["submodule", "update", "--init", "--recursive", "--checkout"]
    if force:
        git(["submodule", "foreach", "-q", "--recursive", "git reset -q --hard && git clean -qfdx"], cwd=root)
        args.append("--force")
    run_git(args, root)


def merge_origin(worktree: Path) -> bool:
    """Fetch origin and merge origin/main when it moved; sources follow the merged pins."""
    run_git(["fetch", "-q", "origin"], worktree)
    if not int(git(["rev-list", "--count", "HEAD..origin/main"], cwd=worktree)):
        return False
    run_git(["merge", "-q", "--no-edit", "origin/main"], worktree, interactive=True)
    restore_sources(worktree)
    return True


def prepare_worktree(root: Path) -> Path:
    """Create or force-reset the kept worktree at the live HEAD, then bring origin/main in."""
    worktree = home_path() / "worktrees/skillset" / BRANCH
    head = git(["rev-parse", "HEAD"], cwd=root)
    registered = [Path(line.removeprefix("worktree ")).resolve()
                  for line in git(["worktree", "list", "--porcelain"], cwd=root).splitlines()
                  if line.startswith("worktree ")]
    if worktree.is_dir() and worktree.resolve() in registered:
        git(["reset", "-q", "--hard", head], cwd=worktree)
        git(["checkout", "-q", "-B", BRANCH], cwd=worktree)
    else:
        git(["worktree", "prune"], cwd=root)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-B", BRANCH, str(worktree), head], cwd=root)
    git(["submodule", "sync", "-q", "--recursive"], cwd=worktree)
    restore_sources(worktree, force=True)
    try:
        merge_origin(worktree)
    except ReconcileError as error:
        raise ReconcileError(f"{error}; merge origin/main into the live checkout by hand, then rerun") from None
    return worktree


def inspect_source(name: str, source: SourceCatalog) -> str:
    """Fetch the branch tips #74 compares and report what this run does with the source."""
    def tip(url: str, branch: str) -> str:
        git(["fetch", "-q", url, f"refs/heads/{branch}"], cwd=source.path)
        return git(["rev-parse", "FETCH_HEAD"], cwd=source.path)

    return update_marker(name, source, tip) or "up to date"


def finish(root: Path, worktree: Path) -> None:
    """Fast-forward the live checkout to the worktree, restore its sources and install."""
    try:
        run_git(["merge", "-q", "--ff-only", BRANCH], root, interactive=True)
    except ReconcileError:
        raise ReconcileError("the live checkout gained commits during the run and cannot fast-forward; "
                             "pushed updates are safe on origin, and a rerun finishes") from None
    restore_sources(root)
    if subprocess.run([str(root / "scripts/reconcile-skills.sh")], cwd=root, check=False).returncode:
        raise ReconcileError("install failed; run the skill selector and choose Install")


def run(root: Path) -> int:
    require_primary_checkout(root)
    # Finishes a run that was killed between the fast-forward and this restore.
    restore_sources(root)
    if reason := update_blocker(root):
        raise ReconcileError(reason)
    worktree = prepare_worktree(root)
    catalog = load_catalog(worktree)
    failed = False
    for path in parse_modules(worktree):
        name = path.removeprefix("sources/")
        try:
            print(f"{name}: {inspect_source(name, catalog.sources[name])}")
        except ReconcileError as error:
            print(f"{name}: failed: {error}")
            failed = True
    finish(root, worktree)
    return int(failed)


def main(argv: list[str]) -> int:
    if argv:
        print("usage: scripts/update-sources.sh", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(line_buffering=True)
    root = Path(__file__).resolve().parent.parent
    try:
        return run(root)
    except (ReconcileError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
