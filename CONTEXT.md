# Skillset

Skillset pins skill sources and projects an explicit selection of their skills
onto local harnesses.

## Language

**Source**:
A pinned upstream repository under `sources/<owner>/<repo>` that owns skill contents.
_Avoid_: Repo, upstream, submodule (when meaning the concept)

**Skill**:
A directory containing exactly one `SKILL.md`. It is identified by its source and
its frontmatter name, written `owner/repo:name`; the folder it sits in is not
part of its identity.
_Avoid_: Plugin, package

**Catalog**:
The skills the selector offers for selection, discovered across all sources.
_Avoid_: Inventory, available list

**Selection**:
The set of skills chosen to be installed.
_Avoid_: Enabled skills, install list

**Profile**:
A named selection. Which machine, person, or harness uses a profile is a
separate concern; v1 has exactly one profile, which is unnamed.
_Avoid_: Config, preset

**Harness**:
An agent environment that consumes installed skills, such as `~/.claude` or `~/.agents`.
_Avoid_: Consumer, target, agent
