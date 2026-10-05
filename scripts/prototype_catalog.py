"""PROTOTYPE - throwaway catalog loader for prototype_selector.py. Not production code.

Discovery uses the placeholder from "What counts as a catalog skill...":
reconciler-valid, and only the shallowest copy of a name within a source.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reconcile_skills as rk  # noqa: E402


def primary_root() -> Path:
    """Sources are submodules of the primary checkout; worktrees don't have them."""
    if "SKILLSET_ROOT" in os.environ:
        return Path(os.environ["SKILLSET_ROOT"])
    here = Path(__file__).resolve().parent
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            cwd=here, text=True, capture_output=True, check=True).stdout.strip()
    return Path(common).parent


ROOT = primary_root()


@dataclass
class Skill:
    source: str          # owner/repo
    name: str
    path: str            # repo-relative directory
    group: str           # "" when it folds into the source
    description: str
    body: str
    manual_only: bool
    file_count: int

    @property
    def key(self) -> str:
        return f"{self.source}:{self.name}"


def split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), "\n".join(lines[end + 1:]).lstrip("\n")


def sources() -> list[str]:
    out = subprocess.run(["git", "config", "--file", str(ROOT / ".gitmodules"), "--get-regexp", r"submodule\..*\.path"],
                         text=True, capture_output=True, check=True).stdout
    return sorted(line.split()[1] for line in out.splitlines())


def load_catalog() -> list[Skill]:
    skills: list[Skill] = []
    for module in sources():
        source = module.removeprefix("sources/")
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT / module, text=True,
                                 capture_output=True, check=True).stdout.splitlines()
        descriptors = sorted((PurePosixPath(p) for p in tracked if PurePosixPath(p).name == "SKILL.md"),
                             key=lambda p: (len(p.parts), str(p)))
        valid: dict[str, tuple[PurePosixPath, dict, str, int]] = {}
        for desc in descriptors:
            rel = desc.parent
            # A directory with a nested SKILL.md is rejected by the reconciler.
            prefix = "" if rel == PurePosixPath(".") else f"{rel}/"
            if sum(str(d).startswith(prefix) for d in descriptors) != 1:
                continue
            try:
                name = rk.skill_name(ROOT / module / rel)
            except rk.ReconcileError:
                continue
            if name == "synced" or name in valid:   # shallowest copy wins
                continue
            meta, body = split_frontmatter((ROOT / module / desc).read_text(encoding="utf-8"))
            valid[name] = (rel, meta, body, sum(p.startswith(prefix) for p in tracked))
        parents = [rel.parent for rel, *_ in valid.values()]
        common = PurePosixPath(os.path.commonpath([str(p) for p in parents])) if parents else PurePosixPath(".")
        for name, (rel, meta, body, count) in valid.items():
            folder = rel.parent.relative_to(common)
            group = str(meta.get("category") or (folder.parts[0] if folder.parts else ""))
            skills.append(Skill(
                source=source, name=name, path=f"{module}/{rel}", group=group,
                description=" ".join(str(meta.get("description", "")).split()),
                body=body, manual_only=meta.get("disable-model-invocation") is True,
                file_count=count))
    # Groups with a single skill fold into the source.
    sizes: dict[tuple[str, str], int] = {}
    for s in skills:
        sizes[(s.source, s.group)] = sizes.get((s.source, s.group), 0) + 1
    for s in skills:
        if sizes[(s.source, s.group)] == 1:
            s.group = ""
    return sorted(skills, key=lambda s: (s.source, s.group, s.name))


def load_selection(catalog: list[Skill]) -> tuple[set[str], dict[str, str]]:
    """Read today's path-based skills.txt and convert it to owner/repo:name.

    Returns (selected keys, off-catalog keys -> reason).
    """
    by_path = {s.path: s for s in catalog}
    selected: set[str] = set()
    missing: dict[str, str] = {}
    for raw in (ROOT / "skills.txt").read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if value in by_path:
            selected.add(by_path[value].key)
        else:
            parts = PurePosixPath(value).parts
            key = f"{parts[1]}/{parts[2]}:{parts[-1]}"
            selected.add(key)
            missing[key] = "missing at the pinned commit"
    return selected, missing


if __name__ == "__main__":
    cat = load_catalog()
    print(len(cat), "skills")
    from collections import Counter
    print(Counter((s.source, s.group) for s in cat))
    names = Counter(s.name for s in cat)
    print("collisions:", {n: [s.source for s in cat if s.name == n] for n, c in names.items() if c > 1})
    print(load_selection(cat))
