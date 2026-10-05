"""Read and validate sources and selected skills in an explicit checkout."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
PLAIN_NAME = re.compile(r"name: ([a-z0-9]+(?:-[a-z0-9]+)*)\Z", re.ASCII)


class ReconcileError(Exception):
    pass


def git(args: list[str], cwd: Path, *, check: bool = True) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
               GIT_LITERAL_PATHSPECS="1")
    result = subprocess.run(["git", *args], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode:
        raise ReconcileError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def parse_modules(root: Path) -> dict[str, str]:
    modules_file = root / ".gitmodules"
    if not modules_file.is_file() or modules_file.is_symlink():
        raise ReconcileError(".gitmodules is missing or is not a regular file")
    # --list succeeds for an empty file but still fails for malformed Git config.
    config = git(["config", "--null", "--file", str(modules_file), "--list"], cwd=root)
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
    for row in git(["ls-tree", "-r", "--full-tree", "HEAD"], cwd=root).splitlines():
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

def validate_sources(root: Path, modules: dict[str, str]) -> None:
    for path, expected in sorted(modules.items()):
        source = root / path
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


def validate_skill(root: Path, module: str, value: str) -> tuple[str, str]:
    """Validate one skill directory under its configured source."""
    skill_dir = root / value
    rel = skill_dir.relative_to(root / module)
    cursor = root / module
    for part in rel.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ReconcileError(f"selected skill path traverses a symlink: {value}")
    if not skill_dir.is_dir():
        raise ReconcileError(f"selected skill directory is missing: {value}")
    # Reject any selected subtree content Git does not track, including ignored files.
    extras = git(["ls-files", "--others", "--exclude-standard", "--ignored", "-z", "--", str(rel)], cwd=root / module)
    if extras:
        raise ReconcileError(f"selected skill contains untracked or ignored content: {value}")
    descriptor = str(rel / "SKILL.md")
    tracked = git(["ls-files", "-z", "--", str(rel)], cwd=root / module).split("\0")
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
    return name, str(skill_dir.absolute())


def selected_skills(root: Path, modules: dict[str, str]) -> list[tuple[str, str]]:
    selection = root / "skills.txt"
    if selection.is_symlink() or not selection.is_file():
        raise ReconcileError("skills.txt is missing or is not a regular file")
    try:
        rows = selection.read_text(encoding="utf-8").splitlines()
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
        name, skill_path = validate_skill(root, module, value)
        if name in names:
            raise ReconcileError(f"selected skill-name collision: {name}\n  {names[name]}\n  {value}")
        names[name] = value
        result.append((name, skill_path))
    return sorted(result)
