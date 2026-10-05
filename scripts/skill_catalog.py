"""Discover skills and resolve install-directory targets in an explicit checkout."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
PLAIN_NAME = re.compile(r"name: ([a-z0-9]+(?:-[a-z0-9]+)*)\Z", re.ASCII)
HARNESSES = {"claude-code", "codex", "pi", "opencode"}
# Install directories are relative to HOME; harness order defines precedence.
AGENTS_DIR = Path(".agents/skills")
CLAUDE_DIR = Path(".claude/skills")
INSTALL_HARNESSES = {
    AGENTS_DIR: ("codex", "pi", "opencode"),
    CLAUDE_DIR: ("claude-code", "opencode"),
}
SELECTION_PATH = "skills.txt"
SELECTION_HEADER = "# Written by the skill selector. Comments and ordering are not kept."


class ReconcileError(Exception):
    pass


@dataclass(frozen=True)
class InvalidSkill:
    path: str
    error: str


@dataclass
class SourceCatalog:
    skills: dict[str, str]
    invalid: dict[str, list[InvalidSkill]]
    variants: dict[str, dict[str, str]]


@dataclass(frozen=True)
class SelectionLine:
    value: str
    number: int
    error: str = ""
    name: str | None = None


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


def read_variants(root: Path, modules: dict[str, str]) -> dict[str, dict[str, str]]:
    """Read the required declaration of source-relative harness variant trees."""
    file = root / "sources.toml"
    if file.is_symlink() or not file.is_file():
        raise ReconcileError("sources.toml is missing or is not a regular file")
    if git(["ls-files", "-z", "--", "sources.toml"], cwd=root) != "sources.toml\0":
        raise ReconcileError("required install input is not tracked: sources.toml")
    try:
        with file.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ReconcileError(f"invalid sources.toml: {error}") from error
    result = {}
    for name, entry in config.items():
        module = f"sources/{name}"
        if module not in modules:
            raise ReconcileError(f"sources.toml names an unknown source: {name}")
        if not isinstance(entry, dict):
            raise ReconcileError(f"sources.toml source must be a table: {name}")
        unknown = set(entry) - {"variants"}
        if unknown:
            raise ReconcileError(f"sources.toml source has unknown fields: {name}: {sorted(unknown)}")
        variants = entry.get("variants", {})
        if not isinstance(variants, dict):
            raise ReconcileError(f"sources.toml variants must be a table: {name}")
        for harness, value in variants.items():
            if harness not in HARNESSES:
                raise ReconcileError(f"sources.toml names an unknown harness: {name}: {harness}")
            if (not isinstance(value, str) or not value or value == "." or "\\" in value
                    or Path(value).is_absolute() or Path(value).as_posix() != value
                    or any(p in {".", ".."} for p in Path(value).parts)
                    or any(ord(c) < 32 for c in value)):
                raise ReconcileError(f"sources.toml variant must be a normalized source-relative tree path: {name}: {harness}")
            directory = root / module / value
            if not directory.is_dir() or directory.resolve() != directory.absolute():
                raise ReconcileError(f"sources.toml variant tree is missing or traverses a symlink: {name}: {value}")
        result[module] = variants
    return result


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


def validate_skill(
    root: Path, module: str, value: str, tracked: set[str], extras: set[str],
) -> tuple[str, str]:
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
    if any(Path(path).is_relative_to(rel) for path in extras):
        raise ReconcileError(f"selected skill contains untracked or ignored content: {value}")
    descriptor = str(rel / "SKILL.md")
    if descriptor not in tracked:
        raise ReconcileError(f"selected SKILL.md is not tracked: {value}")
    if sum(Path(path).is_relative_to(rel) and Path(path).name == "SKILL.md"
           for path in tracked) != 1:
        raise ReconcileError(f"selected directory contains additional SKILL.md files: {value}")
    for directory, children, _ in os.walk(skill_dir, followlinks=False):
        children.sort()
        if any((Path(directory) / child).is_symlink() for child in children):
            raise ReconcileError(f"selected skill contains directory symlinks: {value}")
    name = skill_name(skill_dir)
    if name == "synced":
        raise ReconcileError("skill name 'synced' is reserved by Claude Code")
    return name, str(skill_dir.absolute())


def discover_catalog(root: Path, modules: dict[str, str]) -> dict[str, SourceCatalog]:
    variants = read_variants(root, modules)
    catalog = {}
    for module in sorted(modules):
        source = root / module
        tracked = {}
        for row in git(["ls-files", "--stage", "-z"], cwd=source).split("\0"):
            if row:
                metadata, path = row.split("\t", 1)
                tracked[path] = metadata.split()[0]
        # Without exclude rules, --others includes ignored files as well.
        extras = set(filter(None, git(["ls-files", "--others", "-z"], cwd=source).split("\0")))
        if "160000" in tracked.values():
            raise ReconcileError(f"nested source submodules are unsupported: {module}")
        trees = variants.get(module, {})
        for tree in trees.values():
            if not any(path.startswith(tree + "/") for path in tracked):
                raise ReconcileError(f"sources.toml variant tree is not tracked: {module}: {tree}")
        candidates: dict[str, list[str]] = {}
        invalid: dict[str, list[InvalidSkill]] = {}
        variant_skills: dict[str, dict[str, str]] = {tree: {} for tree in trees.values()}
        tracked_paths = set(tracked)
        for path in sorted(tracked):
            if Path(path).name != "SKILL.md":
                continue
            matching_trees = [tree for tree in variant_skills if Path(path).is_relative_to(tree)]
            directory = (source / path).parent
            value = directory.relative_to(root).as_posix()
            try:
                name, target = validate_skill(root, module, value, tracked_paths, extras)
            except ReconcileError as error:
                if matching_trees:
                    raise ReconcileError(f"invalid variant skill in {value}: {error}") from error
                invalid.setdefault(directory.name, []).append(InvalidSkill(str(directory.absolute()), str(error)))
            else:
                if matching_trees:
                    for tree in matching_trees:
                        copies = variant_skills[tree]
                        if name in copies:
                            raise ReconcileError(
                                f"repeated variant skill name in {module}: {tree}: {name}\n"
                                f"  {copies[name]}\n  {target}"
                            )
                        copies[name] = target
                else:
                    candidates.setdefault(name, []).append(target)
        repeats = {name: paths for name, paths in candidates.items() if len(paths) > 1}
        if repeats:
            copies = "\n".join(f"  {name}:\n    " + "\n    ".join(paths)
                               for name, paths in repeats.items())
            raise ReconcileError(f"undeclared repeated canonical skill names in {module}:\n{copies}")
        catalog[module] = SourceCatalog(
            {name: paths[0] for name, paths in candidates.items()}, invalid,
            {harness: variant_skills[tree] for harness, tree in trees.items()},
        )
    return catalog


def read_selection_text(root: Path) -> str:
    """Read the exact file content, including comments and line endings."""
    selection = root / SELECTION_PATH
    if selection.is_symlink() or not selection.is_file():
        raise ReconcileError(f"{SELECTION_PATH} is missing or is not a regular file")
    try:
        return selection.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ReconcileError(f"could not read {SELECTION_PATH} as UTF-8") from error


def read_selection_lines(root: Path) -> list[SelectionLine]:
    """Retain every selection entry so the selector can repair invalid lines."""
    return parse_selection(read_selection_text(root))


def parse_selection(content: str) -> list[SelectionLine]:
    lines = []
    seen = set()
    for number, raw in enumerate(content.splitlines(), 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+):([^:/\\\s]+)", value)
        error = ""
        valid = (match is not None and not any(part in {".", ".."} for part in match.group(1, 2))
                 and not any(ord(c) < 32 for c in value))
        if not valid:
            error = f"bad syntax; expected owner/repo:name: {value}"
        elif value in seen:
            error = f"duplicate selection line: {value}"
        seen.add(value)
        lines.append(SelectionLine(value, number, error, match.group(3) if valid else None))
    return lines


def read_selection(root: Path) -> dict[str, int]:
    """Read validated name lines without requiring catalog resolution."""
    lines = read_selection_lines(root)
    for line in lines:
        if line.error:
            raise ReconcileError(f"{SELECTION_PATH}:{line.number}: {line.error}")
    return {line.value: line.number for line in lines}


def selection_error(value: str, catalog: dict[str, SourceCatalog]) -> str:
    """Explain why a syntactically valid line does not resolve to a skill."""
    source_name, name = value.split(":")
    source = catalog.get(f"sources/{source_name}")
    if source is None:
        return f"unknown source: {source_name}"
    if name not in source.skills:
        invalid = source.invalid.get(name, [])
        if invalid:
            reasons = "\n  ".join(entry.error for entry in invalid)
            return f"selected name is invalid: {value}\n  {reasons}"
        return f"name is missing at the pinned commit: {value}"
    return ""


def write_selection(root: Path, selection: Iterable[str]) -> str:
    """Write selection lines in the selector's stable format and return that text."""
    content = "\n".join([SELECTION_HEADER, *sorted(selection)]) + "\n"
    (root / SELECTION_PATH).write_text(content, encoding="utf-8")
    return content


def selected_skills(root: Path, catalog: dict[str, SourceCatalog]) -> dict[tuple[Path, str], str]:
    names: dict[str, str] = {}
    result = {}
    for value, number in read_selection(root).items():
        source_name, name = value.split(":")
        location = f"{SELECTION_PATH}:{number}"
        error = selection_error(value, catalog)
        if error:
            raise ReconcileError(f"{location}: {error}")
        source = catalog[f"sources/{source_name}"]
        if name in names:
            raise ReconcileError(f"{location}: same name selected from two sources: {name}\n  {names[name]}\n  {value}")
        names[name] = value
        for directory, harnesses in INSTALL_HARNESSES.items():
            # Choose the tree first: a gap in it uses canonical, not a later tree.
            copies = next((source.variants[harness] for harness in harnesses
                           if harness in source.variants), {})
            result[directory, name] = copies.get(name, source.skills[name])
    return dict(sorted(result.items()))
