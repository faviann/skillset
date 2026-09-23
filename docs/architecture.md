# Architecture

## Committed inputs

Each independent source is a Git submodule below `sources/<owner>/<repo>`.
`.gitmodules` records its URL and path; the superproject gitlink records the
exact commit. `skills.txt` lists selected skill directories as paths relative
to the skillset checkout. It is only a selection list: source membership does
not make other skills effective. The reconciler derives each public skill name
from `SKILL.md` frontmatter and requires it to match the directory name. There
is no SHA lockfile, copied skill content, or directory aliasing.

The initial selection has 31 entries. It preserves the prior installable set:
skills outside `in-progress`, `deprecated`, and `node_modules`, minus
`migrate-to-shoehorn`, `obsidian-vault`, and `work-on`. The result is recorded
as paths, rather than reopening an include-all rule that could silently grow.

## Reconciliation and ownership

The standard-library Python reconciler validates the committed skillset,
submodule pins and clean source checkouts before touching consumer links. It
projects selected source directories as absolute symlinks into
`~/.agents/skills` and `~/.claude/skills`. It neither fetches nor initializes
submodules. `--check` performs the same validation and reports drift without
writing.

Ownership is stored at `~/.local/state/skillset/ownership.json`, keyed by
harness and skill name and recording the exact symlink target. Existing links
are never adopted based on their name, target prefix, or similarity. An
unrecorded entry at a selected name is a collision even if it points at the
right source. Stale links are removed only when the current symlink text
exactly matches a previously recorded target. This permits cleanup after a
source disappears or the skillset checkout moves, without inspecting the
missing target or guessing from its path.

Before changing links, reconciliation atomically writes a pending ownership
record containing both prior and desired targets. It then removes stale links,
creates missing links, and atomically writes the final record. If interrupted,
the pending record lets a later run finish or repeat those exact operations.
This is recoverable convergence, not a filesystem transaction: a failed
operation can leave some links changed. A later run retries from the pending
record; a changed or mismatched entry fails closed for manual inspection.

The reconciler refuses a linked worktree because both destinations are shared
by a user's primary checkout. It also rejects symlinked harness/state
directories, real-entry collisions, selected identity collisions, invalid or
ambiguous identity frontmatter, dirty/uninitialized/wrong-revision sources,
and uncommitted tracked skillset changes. Source directories remain mutable:
links expose their current files, so a clean pinned-state check only proves
their state at that moment.

## Frontmatter subset

No YAML dependency is required. Identity parsing supports one direct, plain
`name: skill-name` scalar between `---` delimiters. Quoted, folded, duplicate,
merged, commented, or otherwise unsupported `name` syntax fails closed. The
selected directory name must be the same valid 1–64 character lowercase ASCII
name defined by the [Agent Skills specification](https://agentskills.io/specification).
This parser reads only that identity field; it does not interpret the rest of
the YAML frontmatter.
