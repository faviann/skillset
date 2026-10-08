#!/usr/bin/env python3
"""Update sources one at a time in a kept worktree and publish each accepted pin to main."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from reconcile_skills import (
    ensure_skillset_committed,
    home_path,
    require_primary_checkout,
    selection_install_blocker,
    stale_upstreams,
)
from skill_catalog import (
    Catalog,
    ReconcileError,
    SourceCatalog,
    canonical_url,
    git,
    git_env,
    load_catalog,
    parse_modules,
    path_prefixes,
    read_selection_text,
    selection_uncommitted,
    update_marker,
)

BRANCH = "update-sources"
PROMPT = "[a]ccept and push, [s]kip, [d]iff of changed selected skills? "


@dataclass(frozen=True)
class SourceUpdate:
    name: str
    old: str
    new: str
    upstream: str | None = None


@dataclass(frozen=True)
class Summary:
    commits: int
    added: list[str]
    removed: list[str]
    changed: list[str]
    selected: frozenset[str]
    unpublished: list[str]

    def lines(self, update: SourceUpdate) -> list[str]:
        lines = [f"{update.name} {update.old[:7]} -> {update.new[:7]}, "
                 f"{self.commits} commit{'s' if self.commits != 1 else ''}"]
        for label, names in (("added", self.added), ("removed", self.removed), ("changed", self.changed)):
            if names:
                lines.append(f"  {label}: " + " ".join(name + "*" * (name in self.selected) for name in names))
        if self.selected.intersection(self.changed):
            lines.append("  * selected")
        if self.unpublished:
            lines.append(f"  also publishes {len(self.unpublished)} local commit(s) with this push:")
            lines.extend(f"    {line}" for line in self.unpublished)
        return lines


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
    """Put every source at its pin, as ./setup.sh does; force also discards changes inside them, quietly."""
    args = ["submodule", "update", "--init", "--recursive", "--checkout"]
    if force:
        git(["submodule", "foreach", "-q", "--recursive", "git reset -q --hard && git clean -qfdx"], cwd=root)
        args += ["--force", "-q"]
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
        print(f"cloning every source into {worktree}")
    git(["submodule", "sync", "-q", "--recursive"], cwd=worktree)
    restore_sources(worktree, force=True)
    try:
        merge_origin(worktree)
    except ReconcileError as error:
        raise ReconcileError(f"{error}; merge origin/main into the live checkout by hand, then rerun") from None
    return worktree


def reset_worktree(worktree: Path, checkpoint: str) -> None:
    git(["reset", "-q", "--hard", checkpoint], cwd=worktree)
    restore_sources(worktree, force=True)


def inspect_source(name: str, source: SourceCatalog) -> SourceUpdate | str:
    """Fetch the branch tips #74 compares, then say what to apply or report for this source."""
    tips: dict[str, str] = {}

    def tip(url: str, branch: str) -> str:
        git(["fetch", "-q", url, f"refs/heads/{branch}"], cwd=source.path)
        tips[url] = git(["rev-parse", "FETCH_HEAD"], cwd=source.path)
        return tips[url]

    marker = update_marker(name, source, tip)
    if marker != "update available":
        return marker or "up to date"
    update = SourceUpdate(name, source.module.pin, tips[source.module.url])
    if not source.upstream:
        return update
    canonical = tip(canonical_url(name), source.module.branch)
    return replace(update, upstream=git(["merge-base", canonical, update.new], cwd=source.path))


def commit_update(worktree: Path, update: SourceUpdate, source: SourceCatalog) -> None:
    git(["checkout", "-q", "--detach", update.new], cwd=source.path)
    if update.upstream and update.upstream != source.upstream:
        file = worktree / "sources.toml"
        text = file.read_text(encoding="utf-8")
        if text.count(f'"{source.upstream}"') != 1:
            raise ReconcileError(f'sources.toml must quote "{source.upstream}" exactly once to update {update.name}')
        file.write_text(text.replace(f'"{source.upstream}"', f'"{update.upstream}"'), encoding="utf-8")
    short = git(["rev-parse", "--short", update.new], cwd=source.path)
    run_git(["commit", "-q", "--only", "-m", f"Update {update.name} to {short}", "--",
             f"sources/{update.name}", "sources.toml"], worktree, interactive=True)


def check_installable(worktree: Path, catalog: Catalog) -> None:
    """The install's read-only checks after the catalog loads; the install itself refuses linked worktrees."""
    ensure_skillset_committed(worktree)
    catalog.resolve(read_selection_text(worktree)).install_targets()
    if stale := stale_upstreams(catalog):
        raise ReconcileError("\n".join(stale))


def skill_directories(source: SourceCatalog, name: str) -> set[Path]:
    """The skill's canonical directory and the variant copies installs use, relative to the source."""
    return {Path(target).relative_to(source.path) for target in (source.skills[name], *source.targets(name).values())}


def summarize(worktree: Path, update: SourceUpdate, before: SourceCatalog, after: SourceCatalog,
              selected: frozenset[str]) -> Summary:
    changed_files = git(["diff", "--name-only", "-z", "--no-renames", update.old, update.new], cwd=after.path)
    touched = path_prefixes(map(Path, filter(None, changed_files.split("\0"))))
    changed = [name for name in sorted(set(before.skills) & set(after.skills))
               if any(touched[directory]
                      for directory in skill_directories(before, name) | skill_directories(after, name))]
    commits = int(git(["rev-list", "--count", f"{update.old}..{update.new}"], cwd=after.path))
    unpublished = git(["log", "--format=%h %s", "origin/main..HEAD^"], cwd=worktree).splitlines()
    return Summary(commits, sorted(set(after.skills) - set(before.skills)),
                   sorted(set(before.skills) - set(after.skills)), changed, selected, unpublished)


def show_diff(update: SourceUpdate, before: SourceCatalog, after: SourceCatalog, summary: Summary) -> None:
    directories = sorted({str(directory) for name in summary.changed if name in summary.selected
                          for directory in skill_directories(before, name) | skill_directories(after, name)})
    if not directories:
        print("no selected skill changed")
        return
    subprocess.run(["git", "diff", update.old, update.new, "--", *directories], cwd=after.path,
                   env=git_env(interactive=True), check=False)


def ask() -> str:
    while True:
        try:
            answer = input(PROMPT).strip().lower()
        except EOFError:
            print()
            return "s"
        if answer in ("a", "s", "d"):
            return answer


def update_source(worktree: Path, name: str, before: Catalog) -> tuple[bool, Catalog]:
    """Fetch, bump, review and publish one source; a skip or any failure resets to the checkpoint."""
    checkpoint = git(["rev-parse", "HEAD"], cwd=worktree)
    source = before.sources[name]
    added: list[str] = []
    try:
        update = inspect_source(name, source)
        if isinstance(update, str):
            print(f"{name}: {update}")
            return True, before
        commit_update(worktree, update, source)
        after = load_catalog(worktree)
        selected = frozenset(line.name for line in after.resolve(read_selection_text(worktree)).lines
                             if line.name and line.value.startswith(f"{name}:"))
        summary = summarize(worktree, update, source, after.sources[name], selected)
        added = summary.added
        check_installable(worktree, after)
        print("\n".join(summary.lines(update)))
        while (answer := ask()) == "d":
            show_diff(update, source, after.sources[name], summary)
        if answer == "s":
            print(f"{name}: skipped")
            reset_worktree(worktree, checkpoint)
            return True, before
        if merge_origin(worktree):
            after = load_catalog(worktree)
            check_installable(worktree, after)
        run_git(["push", "-q", "origin", "HEAD:main"], worktree, interactive=True)
        print(f"{name}: pushed")
        return True, after
    except ReconcileError as error:
        print(f"{name}: skipped: {error}")
        if added:
            print(f"  the update adds: {' '.join(added)}")
        reset_worktree(worktree, checkpoint)
        return False, before


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
        ok, catalog = update_source(worktree, path.removeprefix("sources/"), catalog)
        failed |= not ok
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
