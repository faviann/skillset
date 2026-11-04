#!/usr/bin/env python3
"""Inventory what each pinned source ships per harness, and how often it changed across pins.

Reads only Git objects (ls-tree / diff / log); never runs source code.
Usage: inventory.py <skillset-checkout> [--json out.json]
"""
import json, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import PurePosixPath

CATEGORIES = [
    ("plugin-manifest", re.compile(r"(^|/)(\.claude-plugin/(plugin|marketplace)\.json|\.codex-plugin/plugin\.json|\.agents/plugins/marketplace\.json|\.muse-plugin/[^/]+\.json|\.github/plugin/[^/]+\.json|gemini-extension\.json)$")),
    ("hook-config", re.compile(r"(^|/)hooks/hooks\.json$|(^|/)hooks\.json$|(^|/)\.claude/settings(\.local)?\.json$")),
    ("hook-script", re.compile(r"(^|/)hooks/[^/]+$")),
    ("agent", re.compile(r"^(?!docs/)(.*/)?(agents|\.codex/agents|\.opencode/agents?|\.claude/agents)/[^/]+\.(md|toml)$")),
    ("command-prompt", re.compile(r"(^|/)(commands|prompts|\.codex-plugin/prompts)/[^/]+\.(md|toml)$")),
    ("mcp", re.compile(r"(^|/)\.?mcp\.json$")),
    ("harness-extension", re.compile(r"(^|/)(\.opencode/plugins?/|\.pi/extensions/|extensions/)[^/]+\.(ts|js|mjs)$")),
    ("instruction-file", re.compile(r"(^|/)(AGENTS|CLAUDE|GEMINI)\.md$")),
]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def categorize(path, skill_dirs):
    if PurePosixPath(path).parent == PurePosixPath(".") and re.search(r"^(AGENTS|CLAUDE)\.md$", path):
        return "contributor-instructions"
    for name, rx in CATEGORIES:
        if rx.search(path):
            p = PurePosixPath(path)
            inside_skill = any(str(a) in skill_dirs for a in p.parents)
            if inside_skill and name in ("agent", "command-prompt", "instruction-file"):
                return None  # skill reference material, not a harness surface
            return f"{name}@skill" if inside_skill else name
    return None


def skill_dirs_at(repo, rev):
    files = git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()
    return {str(PurePosixPath(f).parent) for f in files if PurePosixPath(f).name == "SKILL.md"}, files


def main():
    root = sys.argv[1]
    out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    paths = [l.split()[1] for l in git(root, "config", "-f", ".gitmodules", "--get-regexp", r"\.path$").splitlines()]
    report = {}
    for sp in paths:
        src = f"{root}/{sp}"
        pin = git(root, "rev-parse", f"HEAD:{sp}").strip()
        sdirs, files = skill_dirs_at(src, pin)
        ships = defaultdict(list)
        for f in files:
            c = categorize(f, sdirs)
            if c:
                ships[c].append(f)
        pins = []
        for c in reversed(git(root, "log", "--format=%H %cs", "--", sp).splitlines()):
            sha, date = c.split()
            try:
                pins.append((git(root, "rev-parse", f"{sha}:{sp}").strip(), date))
            except subprocess.CalledProcessError:
                pass  # commit that removed the source
        dedup = [p for i, p in enumerate(pins) if i == 0 or p[0] != pins[i - 1][0]]
        bumps = []
        for (a, _), (b, date) in zip(dedup, dedup[1:]):
            sd_b, _ = skill_dirs_at(src, b)
            sd_a, _ = skill_dirs_at(src, a)
            changed = git(src, "diff", "--name-only", a, b).splitlines()
            cats = Counter(c for f in changed if (c := categorize(f, sd_a | sd_b)))
            bumps.append({"date": date, "from": a[:8], "to": b[:8], "files": len(changed), "categories": dict(cats),
                          "surface_paths": [f for f in changed if categorize(f, sd_a | sd_b)]})
        report[sp.removeprefix("sources/")] = {
            "pin": pin[:8], "skills": len(sdirs),
            "ships": {k: sorted(v) for k, v in sorted(ships.items())},
            "pins": len(dedup), "bumps": bumps,
        }
    if out_json:
        with open(out_json, "w") as fh:
            json.dump(report, fh, indent=1)
    cols = [c for c, _ in CATEGORIES] + ["contributor-instructions"]
    allcols = sorted({k for r in report.values() for k in r["ships"]}, key=lambda k: (cols.index(k.split("@")[0]), k))
    print("## Ships at pin (file counts)\n")
    print("| source | pin | skills | " + " | ".join(allcols) + " |")
    print("|---" * (3 + len(allcols)) + "|")
    for s, r in report.items():
        print(f"| {s} | {r['pin']} | {r['skills']} | " + " | ".join(str(len(r["ships"].get(c, []))) or "" for c in allcols) + " |")
    print("\n## Pin bumps touching a harness surface\n")
    print("| source | pins | bumps | bumps touching surface | surface categories changed (bump count) |")
    print("|---|---|---|---|---|")
    for s, r in report.items():
        touching = [b for b in r["bumps"] if b["categories"]]
        cc = Counter(c for b in touching for c in b["categories"])
        print(f"| {s} | {r['pins']} | {len(r['bumps'])} | {len(touching)} | " + ", ".join(f"{k} ({v})" for k, v in sorted(cc.items())) + " |")


if __name__ == "__main__":
    main()
