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

**Selection draft**:
The selector's unsaved edits to the selection, compared against the file they
were loaded from and against the committed selection.
_Avoid_: Working selection, pending changes

**Profile**:
A named selection. Which machine, person, or harness uses a profile is a
separate concern; v1 has exactly one profile, which is unnamed.
_Avoid_: Config, preset

**Harness**:
An agent environment that reads skills from one or more install directories:
Claude Code, Codex, Pi or OpenCode.
_Avoid_: Consumer, agent

**Install directory**:
A directory that skillset links skills into, `~/.agents/skills` or
`~/.claude/skills`. Several harnesses can read the same install directory.
_Avoid_: Consumer directory, target, destination

**Variant**:
A copy of a skill that its source adapts for one harness and ships beside the
skill's canonical copy. The catalog shows only canonical copies; each install
directory gets a variant only when a harness that reads it has one.
_Avoid_: Port, harness copy, mirror

**Install**:
Make every install directory match the committed selection, adding and removing
skill links.
_Avoid_: Deploy, reconcile, sync

**Live checkout**:
The one skillset checkout that installed skill links point into. Committed changes in it
go live when installed.
_Avoid_: Deployment clone, authoring clone (when meaning this one)
