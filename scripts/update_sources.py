#!/usr/bin/env python3
"""Update sources one at a time in a kept worktree and publish each accepted pin to main."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
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
    UPDATE_AVAILABLE,
)

BRANCH = "update-sources"
PROMPT = "[a]ccept and push, [s]kip, [d]iff of changed selected skills? "
Log = Callable[[str], None]


class MergeFailed(ReconcileError):
    pass


@dataclass(frozen=True)
class SourceUpdate:
    name: str
    old: str
    new: str
    upstream: str | None = None


@dataclass(frozen=True)
class Summary:
    commits: list[str]
    added: list[str]
    removed: list[str]
    changed: dict[str, str]
    selected: frozenset[str]
    unpublished: list[str]

    def lines(self, update: SourceUpdate, *, named: bool = True) -> list[str]:
        count = len(self.commits)
        overview = f"{count} commit{'s' if count != 1 else ''} · {update.old[:7]} → {update.new[:7]}"
        lines = [f"{update.name} · {overview}" if named else overview]
        rows = [(name, f"updated · {stat}") for name, stat in self.changed.items()]
        rows += [(name, "removed") for name in self.removed]
        rows += [(name, "added") for name in self.added]
        width = max((len(name) for name, _ in rows), default=0)
        for title, section in (
            ("Skills you use", [row for row in rows if row[0] in self.selected]),
            ("New skills", [row for row in rows if row[1] == "added"]),
            ("Skills you don't use", [row for row in rows if row[0] not in self.selected and row[1] != "added"]),
        ):
            if section:
                lines += ["", title, *(f"  {name:<{width}}  {what}" for name, what in sorted(section))]
        if not self.added and not self.removed:
            lines += ["", "No skills added or removed." if self.changed else "No skill files changed."]
        if self.commits:
            lines += ["", "Commits", *(f"  {line}" for line in self.commits)]
        if self.unpublished:
            count = len(self.unpublished)
            lines += ["", f"Also pushes {count} earlier commit{'s' if count != 1 else ''} not yet on GitHub",
                      *(f"  {line}" for line in self.unpublished)]
        return lines


def run_command(command: list[str], cwd: Path, env: dict[str, str], log: Log) -> int:
    """Run on the terminal when log is print; otherwise send every output line to log."""
    if log is print:
        return subprocess.run(command, cwd=cwd, env=env, check=False).returncode
    # With no terminal, a credential or passphrase prompt fails instead of reading the caller's keys.
    with subprocess.Popen(command, cwd=cwd, env={**env, "SSH_ASKPASS_REQUIRE": "never"},
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", start_new_session=True) as process:
        for line in process.stdout:
            log(line.rstrip("\n"))
    return process.returncode


def run_git(args: list[str], cwd: Path, log: Log, *, interactive: bool = False) -> None:
    if run_command(["git", *args], cwd, git_env(interactive=interactive), log):
        raise ReconcileError(f"git {' '.join(args)} failed")


def off_main(root: Path) -> str | None:
    branch = git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=root, check=False)
    return None if branch == "main" else f"the live checkout is on {branch or 'a detached HEAD'}, not main"


def update_blocker(root: Path) -> str | None:
    """Why the update cannot start, or None."""
    if reason := off_main(root) or selection_install_blocker(root):
        return reason
    if selection_uncommitted(root):
        return "skills.txt has uncommitted changes; save and install first"
    return None


def restore_sources(root: Path, log: Log, *, force: bool = False) -> None:
    git(["submodule", "sync", "-q", "--recursive"], cwd=root)
    args = ["submodule", "update", "--init", "--recursive", "--checkout"]
    if force:
        git(["submodule", "foreach", "-q", "--recursive", "git reset -q --hard && git clean -qfdx"], cwd=root)
        args += ["--force", "-q"]
    run_git(args, root, log)


def merge_origin(worktree: Path, log: Log) -> bool:
    run_git(["fetch", "-q", "--no-recurse-submodules", "origin"], worktree, log)
    if not int(git(["rev-list", "--count", "HEAD..origin/main"], cwd=worktree)):
        return False
    try:
        run_git(["merge", "-q", "--no-edit", "origin/main"], worktree, log, interactive=True)
    except ReconcileError as error:
        raise MergeFailed(str(error)) from None
    restore_sources(worktree, log)
    return True


def prepare_worktree(root: Path, log: Log) -> Path:
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
        log(f"cloning every source into {worktree}")
    restore_sources(worktree, log, force=True)
    try:
        merge_origin(worktree, log)
    except MergeFailed as error:
        raise ReconcileError(f"{error}; merge origin/main into {root} by hand, then rerun") from None
    return worktree


def start(root: Path, log: Log) -> Path:
    """Check the live checkout, restore its sources, and return the update worktree at origin/main."""
    require_primary_checkout(root)
    if reason := off_main(root):
        raise ReconcileError(reason)
    restore_sources(root, log)
    if reason := update_blocker(root):
        raise ReconcileError(reason)
    return prepare_worktree(root, log)


def reset_worktree(worktree: Path, checkpoint: str, log: Log) -> None:
    git(["reset", "-q", "--hard", checkpoint], cwd=worktree)
    restore_sources(worktree, log, force=True)


def inspect_source(name: str, source: SourceCatalog) -> SourceUpdate | str:
    tips: dict[str, str] = {}

    def tip(url: str, branch: str) -> str:
        git(["fetch", "-q", url, f"refs/heads/{branch}"], cwd=source.path)
        tips[url] = git(["rev-parse", "FETCH_HEAD"], cwd=source.path)
        return tips[url]

    marker = update_marker(name, source, tip)
    if marker != UPDATE_AVAILABLE:
        return marker or "up to date"
    update = SourceUpdate(name, source.module.pin, tips[source.module.url])
    if not source.upstream:
        return update
    canonical = tip(canonical_url(name), source.module.branch)
    return replace(update, upstream=git(["merge-base", canonical, update.new], cwd=source.path))


def commit_update(worktree: Path, update: SourceUpdate, source: SourceCatalog, log: Log) -> None:
    git(["checkout", "-q", "--detach", update.new], cwd=source.path)
    if update.upstream and update.upstream != source.upstream:
        file = worktree / "sources.toml"
        text = file.read_text(encoding="utf-8")
        if text.count(f'"{source.upstream}"') != 1:
            raise ReconcileError(f'sources.toml must quote "{source.upstream}" exactly once to update {update.name}')
        file.write_text(text.replace(f'"{source.upstream}"', f'"{update.upstream}"'), encoding="utf-8")
    short = git(["rev-parse", "--short", update.new], cwd=source.path)
    run_git(["commit", "-q", "--only", "-m", f"Update {update.name} to {short}", "--",
             f"sources/{update.name}", "sources.toml"], worktree, log, interactive=True)


def check_installable(worktree: Path, catalog: Catalog) -> None:
    ensure_skillset_committed(worktree)
    catalog.resolve(read_selection_text(worktree)).install_targets()
    if stale := stale_upstreams(catalog):
        raise ReconcileError("\n".join(stale))


def skill_directories(name: str, *sources: SourceCatalog) -> set[Path]:
    return {Path(target).relative_to(source.path) for source in sources
            for target in (source.skills[name], *source.targets(name).values())}


def diffstat(path: Path, old: str, new: str, directories: set[Path]) -> str:
    rows = [line.split("\t", 2) for line in git(
        ["diff", "--numstat", "--no-renames", old, new, "--", *map(str, sorted(directories))],
        cwd=path).splitlines()]
    # Binary files report "-" for both counts.
    added = sum(int(row[0]) for row in rows if row[0] != "-")
    deleted = sum(int(row[1]) for row in rows if row[1] != "-")
    return f"{len(rows)} file{'s' if len(rows) != 1 else ''} +{added} −{deleted}"


def summarize(worktree: Path, update: SourceUpdate, before: SourceCatalog, after: SourceCatalog,
              selected: frozenset[str]) -> Summary:
    changed_files = git(["diff", "--name-only", "-z", "--no-renames", update.old, update.new], cwd=after.path)
    touched = path_prefixes(map(Path, filter(None, changed_files.split("\0"))))
    changed = {}
    for name in sorted(set(before.skills) & set(after.skills)):
        directories = skill_directories(name, before, after)
        if any(touched[directory] for directory in directories):
            changed[name] = diffstat(after.path, update.old, update.new, directories)
    commits = git(["log", "--no-merges", "--reverse", "--format=%h %s", f"{update.old}..{update.new}"],
                  cwd=after.path).splitlines()
    unpublished = git(["log", "--format=%h %s", "origin/main..HEAD^"], cwd=worktree).splitlines()
    return Summary(commits, sorted(set(after.skills) - set(before.skills)),
                   sorted(set(before.skills) - set(after.skills)), changed, selected, unpublished)


@dataclass(frozen=True)
class Prepared:
    """One source's update, committed and validated in the worktree, waiting for accept or discard."""

    worktree: Path
    checkpoint: str
    update: SourceUpdate
    before: SourceCatalog
    after: Catalog
    summary: Summary

    @property
    def source(self) -> SourceCatalog:
        return self.after.sources[self.update.name]

    def diff_args(self) -> list[str] | None:
        """The git diff arguments covering the changed selected skills, or None when none changed."""
        directories = sorted({str(directory) for name in self.summary.changed if name in self.summary.selected
                              for directory in skill_directories(name, self.before, self.source)})
        return ["diff", self.update.old, self.update.new, "--", *directories] if directories else None


def discard(worktree: Path, checkpoint: str, error: ReconcileError, added: list[str], log: Log) -> ReconcileError:
    reset_worktree(worktree, checkpoint, log)
    return ReconcileError(f"{error}\n  the update adds: {' '.join(added)}" if added else str(error))


def prepare(worktree: Path, name: str, before: Catalog, log: Log) -> Prepared | str:
    """Commit the update of one source, or say why there is none. A failure resets the worktree."""
    checkpoint = git(["rev-parse", "HEAD"], cwd=worktree)
    source = before.sources[name]
    added: list[str] = []
    try:
        update = inspect_source(name, source)
        if isinstance(update, str):
            return update
        commit_update(worktree, update, source, log)
        after = load_catalog(worktree)
        selected = frozenset(line.name for line in after.resolve(read_selection_text(worktree)).lines
                             if line.name and line.value.startswith(f"{name}:"))
        summary = summarize(worktree, update, source, after.sources[name], selected)
        added = summary.added
        check_installable(worktree, after)
        return Prepared(worktree, checkpoint, update, source, after, summary)
    except ReconcileError as error:
        raise discard(worktree, checkpoint, error, added, log) from None


def diff_text(prepared: Prepared) -> str:
    args = prepared.diff_args()
    return git(args, cwd=prepared.source.path) if args else "no selected skill changed"


def show_diff(prepared: Prepared) -> None:
    if not (args := prepared.diff_args()):
        print("no selected skill changed")
        return
    subprocess.run(["git", *args], cwd=prepared.source.path, env=git_env(interactive=True), check=False)


def publish(prepared: Prepared, log: Log) -> Catalog:
    """Merge origin/main if it moved, check again, and push. A failure resets the worktree."""
    worktree, after = prepared.worktree, prepared.after
    try:
        if merge_origin(worktree, log):
            after = load_catalog(worktree)
            check_installable(worktree, after)
        run_git(["push", "-q", "origin", "HEAD:main"], worktree, log, interactive=True)
        return after
    except ReconcileError as error:
        raise discard(worktree, prepared.checkpoint, error, prepared.summary.added, log) from None


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
    try:
        prepared = prepare(worktree, name, before, print)
        if isinstance(prepared, str):
            print(f"{name}: {prepared}")
            return True, before
        print("\n".join(prepared.summary.lines(prepared.update)) + "\n")
        while (answer := ask()) == "d":
            show_diff(prepared)
        if answer == "s":
            print(f"{name}: skipped")
            reset_worktree(worktree, prepared.checkpoint, print)
            return True, before
        after = publish(prepared, print)
        print(f"{name}: pushed")
        return True, after
    except ReconcileError as error:
        print(f"{name}: skipped: {error}")
        return False, before


def finish(root: Path, log: Log, env: dict[str, str] | None = None) -> None:
    """Fast-forward the live checkout to the update branch and install with env, or the inherited one."""
    try:
        run_git(["merge", "-q", "--ff-only", BRANCH], root, log, interactive=True)
    except ReconcileError:
        raise ReconcileError("the live checkout gained commits during the run and cannot fast-forward; "
                             "pushed updates are safe on origin, and a rerun finishes") from None
    restore_sources(root, log)
    if run_command([str(root / "scripts/reconcile-skills.sh")], root, dict(os.environ) if env is None else env, log):
        raise ReconcileError("install failed; run the skill selector and choose Install")


def run(root: Path) -> int:
    worktree = start(root, print)
    catalog = load_catalog(worktree)
    failed = False
    for path in parse_modules(worktree):
        ok, catalog = update_source(worktree, path.removeprefix("sources/"), catalog)
        failed |= not ok
    finish(root, print)
    return int(failed)


def main(argv: list[str]) -> int:
    if argv:
        print("usage: scripts/update-sources.sh", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(line_buffering=True)
    root = Path(__file__).resolve().parent.parent
    try:
        return run(root)
    except KeyboardInterrupt:
        print("\ninterrupted; accepted sources are already on origin/main; rerun scripts/update-sources.sh to finish",
              file=sys.stderr)
        return 130
    except (ReconcileError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
