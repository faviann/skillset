#!/usr/bin/env python3
"""Project committed skill selections into local harness directories."""

from __future__ import annotations

import fcntl
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELECTION = ROOT / "skills.txt"
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
PLAIN_NAME = re.compile(r"name: ([a-z0-9]+(?:-[a-z0-9]+)*)\Z", re.ASCII)


class ReconcileError(Exception):
    pass


@dataclass(frozen=True, order=True)
class LinkRecord:
    harness: str
    name: str
    target: str

    @property
    def path(self) -> Path:
        return home_path() / f".{self.harness}" / "skills" / self.name

    @property
    def receipt(self) -> Path:
        return home_path() / f".{self.harness}" / ".skillset" / "receipts" / self.name


@dataclass
class Plan:
    head: str
    removals: list[LinkRecord]
    creations: list[LinkRecord]
    receipt_cleanup: list[LinkRecord]


def home_path() -> Path:
    value = os.environ.get("HOME")
    if not value:
        raise ReconcileError("HOME must be set")
    if not Path(value).is_absolute():
        raise ReconcileError("HOME must be an absolute path")
    return Path(os.path.abspath(value))


def git(args: list[str], cwd: Path = ROOT, *, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
               GIT_LITERAL_PATHSPECS="1")
    result = subprocess.run(["git", *args], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode:
        raise ReconcileError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def require_primary_checkout() -> str:
    try:
        top = Path(git(["rev-parse", "--show-toplevel"])).resolve()
        gd = Path(git(["rev-parse", "--absolute-git-dir"])).resolve()
        common = Path(git(["rev-parse", "--git-common-dir"]))
        common = (common if common.is_absolute() else ROOT / common).resolve()
    except (ReconcileError, OSError) as error:
        raise ReconcileError(f"run from a Git skillset checkout: {error}") from error
    if top != ROOT:
        raise ReconcileError(f"script is not running from its skillset checkout: {ROOT}")
    if gd != common:
        raise ReconcileError(f"skill links belong to the primary checkout at {common.parent}; run scripts/reconcile-skills.sh there")
    return git(["rev-parse", "HEAD"])


def parse_modules() -> dict[str, str]:
    modules_file = ROOT / ".gitmodules"
    if not modules_file.is_file() or modules_file.is_symlink():
        raise ReconcileError(".gitmodules is missing or is not a regular file")
    # --list succeeds for an empty file but still fails for malformed Git config.
    config = git(["config", "--null", "--file", str(modules_file), "--list"])
    paths: dict[str, str] = {}
    urls: dict[str, str] = {}
    for row in config.split("\0"):
        key, _, value = row.partition("\n")
        if not re.fullmatch(r"submodule\..*\.(path|url)", key):
            continue
        section, field = key.rsplit(".", 1)
        mapping = paths if field == "path" else urls
        if section in mapping:
            raise ReconcileError(f"duplicate submodule {field}: {section}")
        mapping[section] = value
    modules: set[str] = set()
    for section, path in paths.items():
        parts = Path(path).parts
        if (len(parts) != 3 or parts[0] != "sources"
                or Path(path).as_posix() != path
                or any(x in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", x)
                       for x in parts[1:])):
            raise ReconcileError(f"submodule path must follow sources/<owner>/<repo>: {path}")
        if path in modules:
            raise ReconcileError(f"duplicate submodule path: {path}")
        if not urls.get(section, "").strip():
            raise ReconcileError(f"submodule has no committed URL: {path}")
        modules.add(path)
    pins: dict[str, str] = {}
    for row in git(["ls-tree", "-r", "--full-tree", "HEAD"]).splitlines():
        metadata, path = row.split("\t", 1)
        mode, kind, oid = metadata.split()
        if mode == "160000" and kind == "commit":
            pins[path] = oid
    if set(pins) != modules:
        raise ReconcileError(
            ".gitmodules and committed submodules differ "
            f"(unconfigured={sorted(set(pins)-modules)}, not-pinned={sorted(modules-set(pins))})"
        )
    return pins

def validate_sources(modules: dict[str, str]) -> None:
    for path, expected in sorted(modules.items()):
        source = ROOT / path
        if source.is_symlink() or not source.is_dir() or not (source / ".git").exists():
            raise ReconcileError(f"source is missing or uninitialized: {path}; explicitly run git submodule update --init --recursive")
        if source.resolve() != source.absolute():
            raise ReconcileError(f"source path traverses a symlink: {path}")
        try:
            top = Path(git(["rev-parse", "--show-toplevel"], cwd=source)).resolve()
            actual = git(["rev-parse", "HEAD"], cwd=source)
        except ReconcileError as error:
            raise ReconcileError(f"source is not an initialized Git checkout: {path}") from error
        if top != source.resolve():
            raise ReconcileError(f"source checkout root does not match its submodule path: {path}")
        if actual != expected:
            raise ReconcileError(f"source revision mismatch for {path}: expected {expected}, found {actual}; check out the committed gitlink explicitly")
        if git(["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"], cwd=source):
            raise ReconcileError(f"source checkout is dirty: {path}")
        nested = git(["ls-tree", "-r", "HEAD"], cwd=source)
        if any(line.startswith("160000 commit ") for line in nested.splitlines()):
            raise ReconcileError(f"nested source submodules are unsupported: {path}")


def ensure_skillset_committed() -> None:
    # Check both comparisons: a staged gitlink can differ even when its worktree
    # has been returned to HEAD. Override local submodule.ignore configuration.
    for staged in ([], ["--cached"]):
        if git(["diff", *staged, "--name-only", "--no-ext-diff",
                "--ignore-submodules=none", "HEAD", "--"]):
            raise ReconcileError(
                "tracked skillset changes are not committed; commit selection, "
                "source pins, and reconciler before deployment"
            )
    for item in (".gitmodules", "skills.txt", "scripts/reconcile-skills.sh",
                 "scripts/reconcile_skills.py"):
        if git(["ls-files", "-z", "--", item]) != item + "\0":
            raise ReconcileError(f"required deployment input is not tracked: {item}")
    if git(["ls-files", "--others", "--exclude-standard", "--", "scripts"]):
        raise ReconcileError("untracked deployment input is present")

def identity(file: Path) -> str:
    """Read an intentionally restricted YAML mapping, never guess an identity.

    The first field is a plain name scalar. Subsequent top-level keys must be
    plain and unique. Indentation is allowed for other fields (descriptions,
    metadata), but not as continuation of name. Other YAML shapes fail closed.
    """
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReconcileError(f"cannot read skill metadata: {file}") from error
    if not lines or lines[0] != "---":
        raise ReconcileError(f"unsupported frontmatter: expected opening --- in {file}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ReconcileError(f"unsupported frontmatter: missing closing --- in {file}") from error
    name = None
    last_key = None
    keys: set[str] = set()
    for row in lines[1:end]:
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        if name is None:
            match = PLAIN_NAME.fullmatch(row)
            if not match:
                raise ReconcileError(f"unsupported frontmatter identity in {file}: plain name field must be first")
            name, last_key = match.group(1), "name"
            keys.add("name")
            continue
        if row.startswith((" ", "\t")):
            if last_key == "name" or row.startswith("\t"):
                raise ReconcileError(f"unsupported frontmatter identity continuation in {file}")
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):(?:[ \t].*)?", row)
        if not match:
            raise ReconcileError(f"unsupported frontmatter identity mapping in {file}")
        last_key = match.group(1)
        if last_key in keys:
            raise ReconcileError(f"unsupported duplicate or ambiguous identity key in {file}")
        keys.add(last_key)
    if name is None or len(name) > 64 or not NAME_PATTERN.fullmatch(name):
        raise ReconcileError(f"invalid or missing skill name in {file}")
    return name


def skill_name(skill_dir: Path) -> str:
    file = skill_dir / "SKILL.md"
    if file.is_symlink() or not file.is_file():
        raise ReconcileError(f"selected skill has no regular SKILL.md: {skill_dir}")
    name = identity(file)
    if skill_dir.name != name:
        raise ReconcileError(f"skill identity does not match its parent directory in {file}: {name}")
    return name

def selected_skills(modules: dict[str, str]) -> list[tuple[str, str]]:
    if SELECTION.is_symlink() or not SELECTION.is_file():
        raise ReconcileError("skills.txt is missing or is not a regular file")
    try:
        rows = SELECTION.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReconcileError("could not read skills.txt as UTF-8") from error
    names: dict[str, str] = {}
    paths: set[str] = set()
    result = []
    for number, raw in enumerate(rows, 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if "\\" in value or value.startswith("/") or any(ord(c) < 32 for c in value) or Path(value).as_posix() != value or any(p in {".", ".."} for p in Path(value).parts):
            raise ReconcileError(f"invalid selection path on skills.txt:{number}")
        if value in paths:
            raise ReconcileError(f"duplicate selected path on skills.txt:{number}: {value}")
        paths.add(value)
        module = next((m for m in sorted(modules, key=len, reverse=True) if value == m or value.startswith(m + "/")), None)
        if module is None:
            raise ReconcileError(f"selection is not under a configured submodule on skills.txt:{number}: {value}")
        skill_dir = ROOT / value
        rel = skill_dir.relative_to(ROOT / module)
        cursor = ROOT / module
        for part in rel.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ReconcileError(f"selected skill path traverses a symlink: {value}")
        if not skill_dir.is_dir():
            raise ReconcileError(f"selected skill directory is missing: {value}")
        # Reject any selected subtree content Git does not track, including ignored files.
        extras = git(["ls-files", "--others", "--exclude-standard", "--ignored", "-z", "--", str(rel)], cwd=ROOT / module)
        if extras:
            raise ReconcileError(f"selected skill contains untracked or ignored content: {value}")
        descriptor = str(rel / "SKILL.md")
        tracked = git(["ls-files", "-z", "--", str(rel)], cwd=ROOT / module).split("\0")
        if descriptor not in tracked:
            raise ReconcileError(f"selected SKILL.md is not tracked: {value}")
        if sum(Path(path).name == "SKILL.md" for path in tracked) != 1:
            raise ReconcileError(f"selected directory contains additional SKILL.md files: {value}")
        for directory, children, _ in os.walk(skill_dir, followlinks=False):
            children.sort()
            if any((Path(directory) / child).is_symlink() for child in children):
                raise ReconcileError(f"selected skill contains directory symlinks: {value}")
        name = skill_name(skill_dir)
        if name == "synced":
            raise ReconcileError("skill name 'synced' is reserved by Claude Code")
        if name in names:
            raise ReconcileError(f"selected skill-name collision: {name}\n  {names[name]}\n  {value}")
        names[name] = value
        result.append((name, str(skill_dir.absolute())))
    return sorted(result)


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
            raise ReconcileError(f"managed destination is not a directory: {cur}")


def owned(receipt: Path, entry: Path, expected: str | None = None) -> bool:
    a, b = lstat(receipt), lstat(entry)
    return bool(a and b and stat.S_ISLNK(a.st_mode) and stat.S_ISLNK(b.st_mode)
                and os.path.samestat(a, b) and os.readlink(receipt) == os.readlink(entry)
                and (expected is None or os.readlink(receipt) == expected))

def local_collision(desired: set[str], retiring: set[Path]) -> None:
    # Include symlinked skills. Directory spelling is not Codex's only identity.
    # This checks user-level consumer entries, not plugins/project/built-in skills.
    for harness in ("agents", "claude"):
        directory = home_path() / f".{harness}/skills"
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
                raise ReconcileError(f"existing consumer skill identity collision: {name} at {child}")



def build_plan() -> Plan:
    head = require_primary_checkout()
    modules = parse_modules()
    validate_sources(modules)
    ensure_skillset_committed()
    selected = selected_skills(modules)
    desired = {(h, n): t for n, t in selected for h in ("agents", "claude")}
    for harness in ("agents", "claude"):
        safe_components(home_path() / f".{harness}/skills", directory=True)
        safe_components(home_path() / f".{harness}/.skillset/receipts", directory=True)
    lock = home_path() / ".agents/.skillset/lock"
    safe_components(lock)
    lock_stat = lstat(lock)
    if lock_stat is not None and not stat.S_ISREG(lock_stat.st_mode):
        raise ReconcileError(f"lock is not a regular file: {lock}")
    removals, creations, receipt_cleanup = [], [], []
    keys = set(desired)
    for h in ("agents", "claude"):
        receipt_dir = home_path() / f".{h}/.skillset/receipts"
        if receipt_dir.is_dir():
            keys |= {(h, p.name) for p in receipt_dir.iterdir()}
    for h, name in sorted(keys):
        entry = home_path() / f".{h}/skills/{name}"
        receipt = home_path() / f".{h}/.skillset/receipts/{name}"
        want = desired.get((h, name))
        rs, es = lstat(receipt), lstat(entry)
        if rs is not None and (not stat.S_ISLNK(rs.st_mode) or len(name) > 64 or NAME_PATTERN.fullmatch(name) is None):
            raise ReconcileError(f"invalid ownership receipt: {receipt}")
        if es is not None and not owned(receipt, entry):
            if want is not None:
                raise ReconcileError(f"unowned or changed consumer entry collision: {entry}")
            # Unowned entries are never in the receipt directory; same name collision is still preserved.
            if rs is not None:
                raise ReconcileError(f"owned consumer entry changed: {entry}")
            continue
        if owned(receipt, entry):
            if want == os.readlink(receipt):
                continue
            else:
                removals.append(LinkRecord(h, name, os.readlink(receipt)))
                if want is not None:
                    creations.append(LinkRecord(h, name, want))
        elif rs is not None and es is None:
            if want == os.readlink(receipt):
                creations.append(LinkRecord(h, name, want))
            else:
                receipt_cleanup.append(LinkRecord(h, name, os.readlink(receipt)))
                if want is not None:
                    creations.append(LinkRecord(h, name, want))
        elif want is not None:
            if es is not None:
                raise ReconcileError(f"unowned consumer entry collision: {entry}")
            creations.append(LinkRecord(h, name, want))
    local_collision({name for name, _ in selected}, {r.path for r in removals})
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
            raise ReconcileError(f"managed destination is not a real directory: {path}")
        return
    ensure_directory(path.parent)
    path.mkdir()
    fsync_dir(path.parent)


def apply_plan(plan: Plan) -> str:
    if not plan.removals and not plan.creations and not plan.receipt_cleanup:
        return "skills are reconciled"
    for harness in ("agents", "claude"):
        root = home_path() / f".{harness}"
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
                raise ReconcileError(f"unowned or changed consumer entry collision: {record.path}")
        else:
            fsync_dir(record.path.parent)
            created += 1
    messages = []
    if removed:
        messages.append(f"removed {removed} owned skill links")
    if created:
        messages.append(f"created {created} skill links")
    return " and ".join(messages) or "skills are reconciled"

def run(check_only: bool) -> int:
    plan = build_plan()
    if check_only:
        discrepancies = [f"missing: {r.path}" for r in plan.creations]
        discrepancies += [f"stale owned link: {r.path}" for r in plan.removals]
        discrepancies += [f"stale receipt: {r.receipt}" for r in plan.receipt_cleanup]
        if discrepancies:
            print("\n".join(discrepancies), file=sys.stderr)
            return 1
        print(f"skills are reconciled at skillset commit {plan.head}")
        return 0
    lock = home_path() / ".agents/.skillset/lock"
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    with os.fdopen(fd, "r+") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ReconcileError(f"lock is not a regular file: {lock}")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        print(apply_plan(build_plan()))
    return 0

def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("usage: scripts/reconcile-skills.sh [--check]", file=sys.stderr)
        return 2
    try:
        return run(argv == ["--check"])
    except (ReconcileError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
