#!/usr/bin/env python3
"""Project committed skill selections into local install directories."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from skill_catalog import (
    AGENTS_DIR,
    CLAUDE_DIR,
    INSTALL_HARNESSES,
    PROFILES_DIR,
    Catalog,
    ReconcileError,
    git,
    git_env,
    identity,
    list_profiles,
    load_catalog,
    profile_path,
    profile_problems,
    read_selection_text,
    valid_name,
)

INSTALL_DIRS = tuple(INSTALL_HARNESSES)


@dataclass(frozen=True, order=True)
class LinkRecord:
    install_dir: Path
    name: str
    target: str

    @property
    def path(self) -> Path:
        return home_path() / self.install_dir / self.name

    @property
    def receipt(self) -> Path:
        return home_path() / self.install_dir.parent / ".skillset" / "receipts" / self.name


@dataclass
class Plan:
    head: str
    removals: list[LinkRecord]
    creations: list[LinkRecord]
    receipt_cleanup: list[LinkRecord]

    @property
    def empty(self) -> bool:
        return not self.removals and not self.creations and not self.receipt_cleanup


@dataclass(frozen=True)
class InstallStatus:
    installed: bool
    problem: str | None = None


def home_path() -> Path:
    value = os.environ.get("HOME")
    if not value:
        raise ReconcileError("HOME must be set")
    if not Path(value).is_absolute():
        raise ReconcileError("HOME must be an absolute path")
    return Path(os.path.abspath(value))


def record_path() -> Path:
    """This home's record of its active profile. It is never committed."""
    return home_path() / ".agents/.skillset/profile"


def choose_profile_hint(root: Path) -> str:
    return (f"profiles at HEAD: {', '.join(list_profiles(root)) or 'none'}\n"
            "choose one with: scripts/reconcile-skills.sh --profile <name>")


def active_profile(root: Path) -> str:
    """The profile this home's record names, which must be committed at HEAD of root."""
    record = record_path()
    safe_components(record)
    st = lstat(record)
    if st is None:
        problem = f"this home has no profile: {record} is missing"
    elif not stat.S_ISREG(st.st_mode):
        problem = f"this home's profile record is not a regular file: {record}"
    else:
        try:
            name = record.read_bytes().decode("utf-8").strip()
        except UnicodeError:
            name = None
        if name is None or not valid_name(name):
            problem = f"this home's profile record holds an invalid name: {record}"
        elif name not in list_profiles(root):
            problem = f"this home's profile {name} is not a profile at HEAD"
        else:
            return name
    raise ReconcileError(f"{problem}\n{choose_profile_hint(root)}")


def require_primary_checkout(root: Path) -> str:
    try:
        top = Path(git(["rev-parse", "--show-toplevel"], cwd=root)).resolve()
        gd = Path(git(["rev-parse", "--absolute-git-dir"], cwd=root)).resolve()
        common = Path(git(["rev-parse", "--git-common-dir"], cwd=root))
        common = (common if common.is_absolute() else root / common).resolve()
    except (ReconcileError, OSError) as error:
        raise ReconcileError(f"run from a Git skillset checkout: {error}") from error
    if top != root:
        raise ReconcileError(f"script is not running from its skillset checkout: {root}")
    if gd != common:
        raise ReconcileError(f"skill links belong to the primary checkout at {common.parent}; run scripts/reconcile-skills.sh there")
    return git(["rev-parse", "HEAD"], cwd=root)


def ensure_skillset_committed(root: Path, profile: str, *, committing: bool = False) -> None:
    """Reject tracked changes except to profiles this home does not install.

    With `committing`, changes to the profile pass too, and so does its file
    being untracked (a new profile), because the caller commits that file next.
    """
    path = profile_path(profile)
    changed: set[str] = set()
    # Check both comparisons: a staged gitlink can differ even when its worktree
    # has been returned to HEAD. Override local submodule.ignore configuration.
    for staged in ([], ["--cached"]):
        paths = git(["diff", *staged, "--name-only", "-z", "--no-ext-diff",
                     "--no-renames", "--ignore-submodules=none", "HEAD", "--"], cwd=root)
        changed.update(filter(None, paths.split("\0")))
    changed = {item for item in changed if not item.startswith(f"{PROFILES_DIR}/")
               or (item == path and not committing)}
    if changed:
        raise ReconcileError(
            "tracked skillset changes are not committed; commit selection, "
            "source pins, and install code first:\n"
            + "\n".join(f"  {item}" for item in sorted(changed))
            + ("\nrun select-skills and choose Install" if path in changed else "")
        )
    for item in (".gitmodules", *([] if committing else [path]), "sources.toml", "scripts/reconcile-skills.sh",
                 "scripts/reconcile_skills.py", "scripts/skill_catalog.py"):
        if git(["ls-files", "-z", "--", item], cwd=root) != item + "\0":
            raise ReconcileError(f"required install input is not tracked: {item}")
    if git(["ls-files", "--others", "--exclude-standard", "--", "scripts"], cwd=root):
        raise ReconcileError("untracked install input is present")


def selection_commit_blocker(root: Path, profile: str | None = None) -> str | None:
    """Why the selector cannot commit the profile (by default the active one), and so cannot install it, or None."""
    try:
        require_primary_checkout(root)
        for marker, operation in (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase")):
            if (root / git(["rev-parse", "--git-path", marker], cwd=root)).exists():
                return f"a {operation} is in progress; finish it first"
        if not git(["symbolic-ref", "--quiet", "HEAD"], cwd=root, check=False):
            return "checkout is on a detached HEAD; switch to a branch"
        ensure_skillset_committed(root, profile or active_profile(root), committing=True)
    except (ReconcileError, OSError) as error:
        return str(error)
    return None


def lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def safe_components(path: Path, *, directory: bool = False) -> None:
    home = home_path()
    if home not in path.parents and path != home:
        raise ReconcileError(f"managed path is outside HOME: {path}")
    cur = home
    st = lstat(cur)
    if st is None or not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ReconcileError(f"HOME is missing or is not a real directory: {home}")
    for i, part in enumerate(path.relative_to(home).parts):
        cur /= part
        st = lstat(cur)
        if st is None:
            continue
        if stat.S_ISLNK(st.st_mode):
            raise ReconcileError(f"managed directory is a symlink: {cur}")
        last = i == len(path.relative_to(home).parts) - 1
        if not last and not stat.S_ISDIR(st.st_mode):
            raise ReconcileError(f"managed parent is not a directory: {cur}")
        if last and directory and not stat.S_ISDIR(st.st_mode):
            raise ReconcileError(f"managed install directory is not a directory: {cur}")


def owned(receipt: Path, entry: Path, expected: str | None = None) -> bool:
    a, b = lstat(receipt), lstat(entry)
    return bool(a and b and stat.S_ISLNK(a.st_mode) and stat.S_ISLNK(b.st_mode)
                and os.path.samestat(a, b) and os.readlink(receipt) == os.readlink(entry)
                and (expected is None or os.readlink(receipt) == expected))

def local_collision(desired: set[str], retiring: set[Path]) -> None:
    # Include symlinked skills. Directory spelling is not Codex's only identity.
    # This checks user-level install directory entries, not plugins/project/built-in skills.
    for install_dir in INSTALL_DIRS:
        directory = home_path() / install_dir
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.name in desired or child in retiring:
                continue
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            try:
                name = identity(child / "SKILL.md")
            except (ReconcileError, OSError, UnicodeError) as error:
                raise ReconcileError(f"ambiguous existing skill metadata: {child}: {error}") from error
            if name in desired:
                raise ReconcileError(f"existing install directory skill identity collision: {name} at {child}")



def choose_profile(root: Path, name: str) -> None:
    """Record name as this home's profile. Recording before installing lets a rerun converge."""
    require_primary_checkout(root)
    if name not in list_profiles(root):
        raise ReconcileError(f"{name!r} is not a profile at HEAD\n{choose_profile_hint(root)}")
    record = record_path()
    safe_components(record)
    record.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged = record.with_name(".profile.new")
    fd = os.open(staged, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"{name}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staged, record)
    fsync_dir(record.parent)


def read_inputs(root: Path) -> tuple[str, str, Catalog]:
    """HEAD, the active profile and the catalog, once the checkout passes the install gates."""
    head = require_primary_checkout(root)
    profile = active_profile(root)
    catalog = load_catalog(root)
    ensure_skillset_committed(root, profile)
    return head, profile, catalog


def read_selection(root: Path) -> tuple[str, dict[tuple[Path, str], str]]:
    head, profile, catalog = read_inputs(root)
    return head, catalog.resolve(read_selection_text(root, profile)).install_targets(profile)


def build_plan(root: Path) -> Plan:
    return plan_links(*read_selection(root))


def stale_upstreams(catalog: Catalog) -> list[str]:
    """Report fork upstreams that are present but not ancestors of the pin.

    Only the (forked) marker reads upstream, so this is a --check finding, not
    an install failure. A missing object (a shallow clone) is not reported.
    """
    stale = []
    for name, source in catalog.sources.items():
        if source.upstream and subprocess.run(
            ["git", "merge-base", "--is-ancestor", source.upstream, "HEAD"], cwd=source.path,
            env=git_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 1:
            stale.append(f"stale upstream in sources.toml: {name}: {source.upstream} is not an "
                         "ancestor of the pin; set it to the merge-base with upstream main")
    return stale


def install_status(root: Path) -> InstallStatus:
    """Plan read-only, without the lock, and report whether HEAD's selection is installed."""
    try:
        return InstallStatus(build_plan(root).empty)
    except (ReconcileError, OSError) as error:
        return InstallStatus(False, str(error))


def plan_links(head: str, desired: dict[tuple[Path, str], str]) -> Plan:
    for install_dir in INSTALL_DIRS:
        safe_components(home_path() / install_dir, directory=True)
        safe_components(home_path() / install_dir.parent / ".skillset/receipts", directory=True)
    lock = home_path() / ".agents/.skillset/lock"
    safe_components(lock)
    lock_stat = lstat(lock)
    if lock_stat is not None and not stat.S_ISREG(lock_stat.st_mode):
        raise ReconcileError(f"lock is not a regular file: {lock}")
    removals, creations, receipt_cleanup = [], [], []
    keys = set(desired)
    for install_dir in INSTALL_DIRS:
        receipt_dir = home_path() / install_dir.parent / ".skillset/receipts"
        if receipt_dir.is_dir():
            keys |= {(install_dir, p.name) for p in receipt_dir.iterdir()}
    for install_dir, name in sorted(keys):
        entry = home_path() / install_dir / name
        receipt = home_path() / install_dir.parent / ".skillset/receipts" / name
        want = desired.get((install_dir, name))
        rs, es = lstat(receipt), lstat(entry)
        if rs is not None and (not stat.S_ISLNK(rs.st_mode) or not valid_name(name)):
            raise ReconcileError(f"invalid ownership receipt: {receipt}")
        if es is not None and not owned(receipt, entry):
            if want is not None:
                raise ReconcileError(f"unowned or changed install directory entry collision: {entry}")
            # Unowned entries are never in the receipt directory; same name collision is still preserved.
            if rs is not None:
                raise ReconcileError(f"owned install directory entry changed: {entry}")
            continue
        if owned(receipt, entry):
            if want == os.readlink(receipt):
                continue
            else:
                removals.append(LinkRecord(install_dir, name, os.readlink(receipt)))
                if want is not None:
                    creations.append(LinkRecord(install_dir, name, want))
        elif rs is not None and es is None:
            if want == os.readlink(receipt):
                creations.append(LinkRecord(install_dir, name, want))
            else:
                receipt_cleanup.append(LinkRecord(install_dir, name, os.readlink(receipt)))
                if want is not None:
                    creations.append(LinkRecord(install_dir, name, want))
        elif want is not None:
            if es is not None:
                raise ReconcileError(f"unowned install directory entry collision: {entry}")
            creations.append(LinkRecord(install_dir, name, want))
    local_collision({name for _, name in desired}, {r.path for r in removals})
    return Plan(head, removals, creations, receipt_cleanup)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ReconcileError(f"managed install directory is not a real directory: {path}")
        return
    ensure_directory(path.parent)
    path.mkdir()
    fsync_dir(path.parent)


def apply_plan(plan: Plan) -> str:
    if plan.empty:
        return "skills are reconciled"
    for install_dir in INSTALL_DIRS:
        root = home_path() / install_dir.parent
        (root / "skills").mkdir(parents=True, exist_ok=True)
        (root / ".skillset/receipts").mkdir(parents=True, exist_ok=True, mode=0o700)
        # Persist directory ancestry as well as each receipt before publication.
        for directory in (root / ".skillset/receipts", root / ".skillset",
                          root / "skills", root, home_path()):
            fsync_dir(directory)
    removed = created = 0
    for record in plan.removals:
        if not owned(record.receipt, record.path, record.target):
            raise ReconcileError(f"owned symlink changed during reconciliation: {record.path}")
        os.unlink(record.path)
        fsync_dir(record.path.parent)
        os.unlink(record.receipt)
        fsync_dir(record.receipt.parent)
        removed += 1
    for record in plan.receipt_cleanup:
        current = lstat(record.receipt)
        if (lstat(record.path) is not None or current is None
                or not stat.S_ISLNK(current.st_mode)
                or os.readlink(record.receipt) != record.target):
            raise ReconcileError(f"orphan receipt changed during reconciliation: {record.receipt}")
        os.unlink(record.receipt)
        fsync_dir(record.receipt.parent)
    for record in plan.creations:
        if lstat(record.receipt) is None:
            os.symlink(record.target, record.receipt)
        current = lstat(record.receipt)
        if (current is None or not stat.S_ISLNK(current.st_mode)
                or os.readlink(record.receipt) != record.target):
            raise ReconcileError(f"ownership receipt changed during reconciliation: {record.receipt}")
        fsync_dir(record.receipt.parent)
        try:
            # Exclusive publication: never replace or adopt an unrelated entry.
            os.link(record.receipt, record.path, follow_symlinks=False)
        except FileExistsError:
            if not owned(record.receipt, record.path, record.target):
                raise ReconcileError(f"unowned or changed install directory entry collision: {record.path}")
        else:
            fsync_dir(record.path.parent)
            created += 1
    messages = []
    if removed:
        messages.append(f"removed {removed} owned skill links")
    if created:
        messages.append(f"created {created} skill links")
    return " and ".join(messages) or "skills are reconciled"

def check(root: Path) -> int:
    """Report every profile's problems and this home's install state, changing nothing."""
    head, profile, catalog = read_inputs(root)
    findings = profile_problems(root, catalog)
    # read_inputs refused a file that differs from HEAD, so this is the copy profile_problems checked.
    resolution = catalog.resolve(read_selection_text(root, profile))
    if not resolution.problems(profile):
        plan = plan_links(head, resolution.install_targets(profile))
        findings += [f"missing: {r.path}" for r in plan.creations]
        findings += [f"stale owned link: {r.path}" for r in plan.removals]
        findings += [f"stale receipt: {r.receipt}" for r in plan.receipt_cleanup]
    findings += stale_upstreams(catalog)
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print(f"skills are reconciled at skillset commit {head}")
    return 0


def install(root: Path) -> int:
    head, selected = read_selection(root)
    plan = plan_links(head, selected)
    lock = home_path() / ".agents/.skillset/lock"
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    with os.fdopen(fd, "r+") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ReconcileError(f"lock is not a regular file: {lock}")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        print(apply_plan(plan_links(head, selected)))
    return 0

def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]) and (len(argv) != 2 or argv[0] != "--profile"):
        print("usage: scripts/reconcile-skills.sh [--check | --profile <name>]", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    try:
        if argv[:1] == ["--profile"]:
            choose_profile(root, argv[1])
        return check(root) if argv == ["--check"] else install(root)
    except (ReconcileError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
