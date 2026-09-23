#!/usr/bin/env python3
"""Project committed skill selections into the two local harness directories."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
SELECTION = ROOT / "skills.txt"
STATE_RELATIVE = Path(".local/state/skillset/ownership.json")
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
NAME_KEY_PATTERN = re.compile(r"^\s*(?:name|['\"]name['\"])\s*:", re.IGNORECASE)
FRONTMATTER_NAME_PATTERN = re.compile(r"name: ([a-z0-9]+(?:-[a-z0-9]+)*)\Z", re.ASCII)


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


@dataclass
class Plan:
    desired: dict[tuple[str, str], str]
    pending_records: set[LinkRecord]
    removals: list[Path]
    removal_targets: dict[Path, set[str]]
    creations: list[LinkRecord]
    state_is_current: bool


def home_path() -> Path:
    value = os.environ.get("HOME")
    if not value:
        raise ReconcileError("HOME must be set")
    path = Path(os.path.abspath(value))
    if not path.is_absolute():
        raise ReconcileError("HOME must be an absolute path")
    return path


def git(args: list[str], cwd: Path = ROOT, *, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReconcileError(f"git {' '.join(args)} failed: {detail}")
    if not check and result.returncode:
        return ""
    return result.stdout.rstrip("\n")


def require_primary_checkout() -> str:
    try:
        top = Path(git(["rev-parse", "--show-toplevel"])).resolve()
        git_dir = Path(git(["rev-parse", "--absolute-git-dir"])).resolve()
        common_value = git(["rev-parse", "--git-common-dir"])
        common_dir = Path(common_value)
        if not common_dir.is_absolute():
            common_dir = ROOT / common_dir
        common_dir = common_dir.resolve()
    except (ReconcileError, OSError) as error:
        raise ReconcileError(f"run from a Git skillset checkout: {error}") from error
    if top != ROOT:
        raise ReconcileError(f"script is not running from its skillset checkout: {ROOT}")
    if git_dir != common_dir:
        raise ReconcileError(
            f"skill links belong to the primary checkout at {common_dir.parent}; "
            "run scripts/reconcile-skills.sh there"
        )
    return git(["rev-parse", "HEAD"])


def parse_modules() -> dict[str, str]:
    modules_file = ROOT / ".gitmodules"
    if not modules_file.is_file() or modules_file.is_symlink():
        raise ReconcileError(".gitmodules is missing or is not a regular file")
    output = git(
        ["config", "--null", "--file", str(modules_file), "--get-regexp", r"^submodule\..*\.path$"],
        check=False,
    )
    modules: dict[str, str] = {}
    if output:
        for item in output.split("\0"):
            if not item:
                continue
            try:
                _key, path = item.split("\n", 1)
            except ValueError as error:
                raise ReconcileError("could not parse submodule paths in .gitmodules") from error
            parts = Path(path).parts
            if (
                len(parts) != 3
                or parts[0] != "sources"
                or any(part in {"", ".", ".."} for part in parts)
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts[1:])
            ):
                raise ReconcileError(
                    f"submodule path must follow sources/<owner>/<repo>: {path}"
                )
            if path in modules:
                raise ReconcileError(f"duplicate submodule path in .gitmodules: {path}")
            modules[path] = path

    tree = git(["ls-tree", "-r", "--full-tree", "HEAD"])
    gitlinks: dict[str, str] = {}
    for line in tree.splitlines():
        if not line:
            continue
        metadata, path = line.split("\t", 1)
        mode, kind, oid = metadata.split()
        if mode == "160000" and kind == "commit":
            gitlinks[path] = oid
    if set(gitlinks) != set(modules):
        missing = sorted(set(gitlinks) - set(modules))
        extra = sorted(set(modules) - set(gitlinks))
        raise ReconcileError(
            f".gitmodules and committed submodules differ (unconfigured={missing}, "
            f"not-pinned={extra})"
        )
    modules.update({path: gitlinks[path] for path in gitlinks})
    return modules


def validate_sources(modules: dict[str, str]) -> None:
    for path, expected in sorted(modules.items()):
        source = ROOT / path
        if source.is_symlink() or not source.is_dir():
            raise ReconcileError(
                f"source is missing or uninitialized: {path}; explicitly run "
                "git submodule update --init --recursive"
            )
        if not (source / ".git").exists():
            raise ReconcileError(
                f"source is missing or uninitialized: {path}; explicitly run "
                "git submodule update --init --recursive"
            )
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
            raise ReconcileError(
                f"source revision mismatch for {path}: expected {expected}, found {actual}; "
                "check out the committed gitlink explicitly"
            )
        dirty = git(
            ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"],
            cwd=source,
        )
        if dirty:
            raise ReconcileError(f"source checkout is dirty: {path}")


def ensure_skillset_committed() -> None:
    dirty = git(
        ["status", "--porcelain=v1", "--untracked-files=no", "--ignore-submodules=all"]
    )
    if dirty:
        raise ReconcileError(
            "tracked skillset changes are not committed; commit the selection, source pins, "
            "and reconciler before deployment"
        )


def skill_name(skill_dir: Path) -> str:
    skill_file = skill_dir / "SKILL.md"
    if skill_file.is_symlink() or not skill_file.is_file():
        raise ReconcileError(f"selected skill has no regular SKILL.md: {skill_dir}")
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReconcileError(f"cannot read selected skill metadata: {skill_file}") from error
    if not lines or lines[0] != "---":
        raise ReconcileError(f"unsupported frontmatter: expected opening --- in {skill_file}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ReconcileError(f"unsupported frontmatter: missing closing --- in {skill_file}") from error
    frontmatter = lines[1:end]
    if any(re.match(r"^\s*<<\s*:", line) for line in frontmatter):
        raise ReconcileError(f"unsupported frontmatter identity merge in {skill_file}")
    fields = [line for line in frontmatter if NAME_KEY_PATTERN.match(line)]
    if len(fields) != 1:
        raise ReconcileError(
            f"unsupported frontmatter identity in {skill_file}: expected one plain name field"
        )
    match = FRONTMATTER_NAME_PATTERN.fullmatch(fields[0])
    if not match:
        raise ReconcileError(
            f"unsupported frontmatter identity in {skill_file}: use plain `name: skill-name`"
        )
    name = match.group(1)
    if len(name) > 64 or NAME_PATTERN.fullmatch(name) is None:
        raise ReconcileError(f"invalid skill name in {skill_file}: {name}")
    if skill_dir.name != name:
        raise ReconcileError(
            f"skill identity does not match its parent directory in {skill_file}: {name}"
        )
    return name


def selected_skills(modules: dict[str, str]) -> list[tuple[str, str]]:
    if SELECTION.is_symlink() or not SELECTION.is_file():
        raise ReconcileError("skills.txt is missing or is not a regular file")
    try:
        rows = SELECTION.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReconcileError("could not read skills.txt as UTF-8") from error

    module_paths = sorted(modules, key=len, reverse=True)
    selections: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    seen_names: dict[str, str] = {}
    for number, raw in enumerate(rows, 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if "\\" in value or value.startswith("/") or any(ord(c) < 32 for c in value):
            raise ReconcileError(f"invalid selection path on skills.txt:{number}")
        parts = Path(value).parts
        if any(part in {".", ".."} for part in parts) or Path(value).as_posix() != value:
            raise ReconcileError(f"selection path is not normalized on skills.txt:{number}: {value}")
        if value in seen_paths:
            raise ReconcileError(f"duplicate selected path on skills.txt:{number}: {value}")
        seen_paths.add(value)
        module = next(
            (candidate for candidate in module_paths if value.startswith(candidate + "/") or value == candidate),
            None,
        )
        if module is None:
            raise ReconcileError(
                f"selection is not under a configured submodule on skills.txt:{number}: {value}"
            )
        skill_dir = ROOT / value
        relative = Path(value).relative_to(module)
        current = ROOT / module
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ReconcileError(f"selected skill path traverses a symlink: {value}")
        if not skill_dir.is_dir():
            raise ReconcileError(f"selected skill directory is missing: {value}")
        name = skill_name(skill_dir)
        if name in seen_names:
            raise ReconcileError(
                f"selected skill-name collision: {name}\n  {seen_names[name]}\n  {value}"
            )
        seen_names[name] = value
        target = str(skill_dir.absolute())
        selections.append((name, target))
    return sorted(selections)


def lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def check_path_components(path: Path, *, directory: bool = True) -> None:
    home = home_path()
    if path != home and home not in path.parents:
        raise ReconcileError(f"managed path is outside HOME: {path}")
    cursor = home
    status = lstat_or_none(cursor)
    if status is None or not stat.S_ISDIR(status.st_mode):
        raise ReconcileError(f"HOME is missing or is not a real directory: {home}")
    if stat.S_ISLNK(status.st_mode):
        raise ReconcileError(f"HOME is a symlink: {home}")
    relative = path.relative_to(home)
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        status = lstat_or_none(cursor)
        if status is None:
            continue
        if stat.S_ISLNK(status.st_mode):
            label = "directory" if directory or index < len(relative.parts) - 1 else "file"
            raise ReconcileError(f"managed {label} is a symlink: {cursor}")
        is_last = index == len(relative.parts) - 1
        if not is_last and not stat.S_ISDIR(status.st_mode):
            raise ReconcileError(f"managed parent is not a directory: {cursor}")
        if is_last and directory and not stat.S_ISDIR(status.st_mode):
            raise ReconcileError(f"managed destination is not a directory: {cursor}")


def ownership_path() -> Path:
    return home_path() / STATE_RELATIVE


def read_ownership() -> tuple[str | None, set[LinkRecord]]:
    path = ownership_path()
    check_path_components(path, directory=False)
    status = lstat_or_none(path)
    if status is None:
        return None, set()
    if not stat.S_ISREG(status.st_mode):
        raise ReconcileError(f"ownership state is not a regular file: {path}")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_json_keys
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ReconcileError(f"ownership state is unreadable or invalid: {path}") from error
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data.get("version") != 1
    ):
        raise ReconcileError(f"unsupported ownership state format: {path}")
    phase = data.get("phase")
    rows = data.get("links")
    if not isinstance(phase, str) or phase not in {"complete", "pending"} or not isinstance(rows, list):
        raise ReconcileError(f"invalid ownership state structure: {path}")
    records: set[LinkRecord] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"harness", "name", "target"}:
            raise ReconcileError(f"invalid ownership record in {path}")
        harness, name, target = row["harness"], row["name"], row["target"]
        if (
            not isinstance(harness, str)
            or harness not in {"agents", "claude"}
            or not isinstance(name, str)
            or NAME_PATTERN.fullmatch(name) is None
            or len(name) > 64
            or not isinstance(target, str)
            or not Path(target).is_absolute()
        ):
            raise ReconcileError(f"invalid ownership record in {path}")
        record = LinkRecord(harness, name, target)
        if record in records:
            raise ReconcileError(f"duplicate ownership record in {path}")
        records.add(record)
    return phase, records


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def build_plan() -> Plan:
    require_primary_checkout()
    modules = parse_modules()
    validate_sources(modules)
    ensure_skillset_committed()
    selected = selected_skills(modules)
    phase, records = read_ownership()
    desired: dict[tuple[str, str], str] = {}
    for name, target in selected:
        desired[("agents", name)] = target
        desired[("claude", name)] = target
    desired_records = {LinkRecord(harness, name, target) for (harness, name), target in desired.items()}
    pending_records = records | desired_records

    for harness in ("agents", "claude"):
        check_path_components(home_path() / f".{harness}", directory=True)
        check_path_components(home_path() / f".{harness}" / "skills", directory=True)

    removals: list[Path] = []
    removal_targets: dict[Path, set[str]] = {}
    creations: list[LinkRecord] = []
    keys = {(record.harness, record.name) for record in records} | set(desired)
    records_by_key: dict[tuple[str, str], set[str]] = {}
    for record in records:
        records_by_key.setdefault((record.harness, record.name), set()).add(record.target)

    for harness, name in sorted(keys):
        link = home_path() / f".{harness}" / "skills" / name
        wanted = desired.get((harness, name))
        known = records_by_key.get((harness, name), set())
        status = lstat_or_none(link)
        if status is None:
            if wanted is not None:
                creations.append(LinkRecord(harness, name, wanted))
            continue
        if not stat.S_ISLNK(status.st_mode):
            owner_text = "owned entry was replaced" if known else "unowned entry collision"
            raise ReconcileError(f"{owner_text}: {link}")
        current = os.readlink(link)
        if wanted is not None and current == wanted:
            if current not in known:
                raise ReconcileError(f"unowned symlink collision (same target): {link} -> {current}")
            continue
        if current not in known:
            expected = sorted(known) or ([wanted] if wanted else [])
            raise ReconcileError(
                f"unowned or changed symlink collision: {link} -> {current}; "
                f"expected one of {expected}"
            )
        removals.append(link)
        removal_targets[link] = known
        if wanted is not None:
            creations.append(LinkRecord(harness, name, wanted))

    final_is_current = phase == "complete" and records == desired_records
    return Plan(
        desired=desired,
        pending_records=pending_records,
        removals=sorted(set(removals)),
        removal_targets=removal_targets,
        creations=sorted(set(creations)),
        state_is_current=final_is_current,
    )


def serialize_state(phase: str, records: Iterable[LinkRecord]) -> bytes:
    payload = {
        "version": 1,
        "phase": phase,
        "links": [
            {"harness": row.harness, "name": row.name, "target": row.target}
            for row in sorted(set(records))
        ],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_state(phase: str, records: Iterable[LinkRecord]) -> None:
    path = ownership_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="ownership.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(serialize_state(phase, records))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_plan(plan: Plan) -> str:
    desired_records = {
        LinkRecord(harness, name, target)
        for (harness, name), target in plan.desired.items()
    }
    if not plan.removals and not plan.creations and plan.state_is_current:
        return "skills are reconciled"

    for harness in ("agents", "claude"):
        (home_path() / f".{harness}" / "skills").mkdir(parents=True, exist_ok=True)
    write_state("pending", plan.pending_records)
    for link in plan.removals:
        status = lstat_or_none(link)
        if status is not None:
            if not stat.S_ISLNK(status.st_mode) or os.readlink(link) not in plan.removal_targets[link]:
                raise ReconcileError(f"owned symlink changed during reconciliation: {link}")
            os.unlink(link)
    for record in plan.creations:
        record.path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(record.target, record.path)
    write_state("complete", desired_records)

    summary = []
    if plan.removals:
        summary.append(f"removed {len(plan.removals)} owned skill links")
    if plan.creations:
        summary.append(f"created {len(plan.creations)} skill links")
    if not summary:
        return "skills are reconciled"
    return " and ".join(summary)


def run(check_only: bool) -> int:
    plan = build_plan()
    if check_only:
        discrepancies = []
        discrepancies.extend(f"missing: {record.path}" for record in plan.creations)
        discrepancies.extend(f"stale owned link: {path}" for path in plan.removals)
        if not plan.state_is_current:
            discrepancies.append("ownership state needs reconciliation")
        if discrepancies:
            print("\n".join(discrepancies), file=sys.stderr)
            return 1
        print(f"skills are reconciled at skillset commit {git(['rev-parse', 'HEAD'])}")
        return 0

    # Lock after a side-effect-free preflight, then recheck all inputs and links.
    state_dir = ownership_path().parent
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "lock"
    check_path_components(lock_path, directory=False)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        plan = build_plan()
        print(apply_plan(plan))
    return 0


def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("usage: scripts/reconcile-skills.sh [--check]", file=sys.stderr)
        return 2
    try:
        return run(check_only=argv == ["--check"])
    except (ReconcileError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
