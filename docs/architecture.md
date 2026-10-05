# Architecture

## Authority and committed inputs

Source repositories own their skill contents. `faviann/skillset` owns only the
aggregate source configuration, explicit selection, and projection into
install directories.

A source's path identifies its canonical upstream repository, independent of
the Git remote used to fetch it. Each source is a Git submodule below
`sources/<upstream-owner>/<upstream-repo>`. A maintained fork may therefore be
used as the submodule URL without changing the source identity. For example,
the canonical `mattpocock/skills` source lives at
`sources/mattpocock/skills` while it is currently fetched from the
`faviann/skills-mattpocock` fork. This keeps source identity stable when fork
names differ or must be renamed because unrelated upstream repositories share
the same repo name. The fork was renamed from `faviann/skills` for that reason.

Forks carry only maintained modifications of their upstream. Skills authored
by faviann live in the first-party source `faviann/agent-skills`, at
`sources/faviann/agent-skills`.

`.gitmodules` records the fetch URL and canonical source path; the superproject
gitlink is the only source-commit lock. `skills.txt` lists selected skills as
`owner/repo:name`, where `owner/repo` is the source folder under `sources/` and
`name` is the frontmatter identity. Path selections are rejected. The catalog
resolves these identities at the pinned commit, so moving a skill within its
source leaves its selection valid when the name stays the same. It discovers
every tracked `SKILL.md` outside declared variant trees and validates its skill
directory. Hidden directories are included, and plugin manifests do not affect
discovery. Names come from frontmatter and must match the directory name. A
single skill at a source root is supported when its identity matches that root
directory. Invalid skills are retained by folder name with their validation
errors; they are not canonical candidates.

The required, tracked `sources.toml` declares harness variant trees by canonical
source identity and source-relative directory. An empty file is valid. For
example, rampstack's Codex and Pi copies are declared as:

```toml
["rampstackco/claude-skills".variants]
codex = "dist/codex/.agents/skills"
pi = "dist/pi/.agents/skills"
```

Supported harness keys are `claude-code`, `codex`, `pi`, and `opencode`. Unknown
sources or harnesses and missing, untracked, or symlinked trees fail validation.
Variant trees are excluded from canonical discovery. Every tracked skill in a
declared variant tree must pass the same per-skill validation as a canonical
copy, even when unselected or present only in that tree. An invalid variant
fails the whole install and `--check`. Valid copies match canonical skills by
frontmatter name; a name present only in a variant tree is not in the catalog.
Repeated names within one variant tree fail because the target is ambiguous.

The catalog uses this fixed harness order for each install directory:

| Install directory | Harness precedence |
| --- | --- |
| `~/.agents/skills` | `codex`, `pi`, `opencode` |
| `~/.claude/skills` | `claude-code`, `opencode` |

For each source and install directory, the first declared harness in that order
chooses the variant tree. If it has no copy of a selected name, that directory
gets the canonical copy, even if a later tree has a copy. With no matching
declaration, it also gets the canonical copy. Thus rampstack's Codex variant
goes into `~/.agents/skills` and its canonical copy into `~/.claude/skills`.

Every install and `--check` rejects repeated valid canonical
names within a source, even if the copies are unselected, and identifies the
copies. Invalid copies do not count as repeats. Each source's tracked and
untracked or ignored files are listed once for catalog discovery, with no
per-skill Git calls. No content is copied and aliases are unsupported.

Selections are explicit decisions, not inherited rules. New source skills
remain unselected.
A selected skill's directory must not contain additional `SKILL.md` files or
directory symlinks that would expose other skills implicitly.

## Install and ownership

The standard-library Python reconciler validates committed inputs, exact
source pins, source cleanliness, skill identities and both destinations before
making changes. It does not fetch, pull, initialize or advance sources. Git
lazy fetching is disabled too. `--check` performs the same validation without
creating a lock or writing state. A concurrent writer can cause a transient
check failure; this is not a globally atomic snapshot.

Each install directory retains per-name symlink receipts:

```text
~/.agents/.skillset/receipts/<name>
~/.claude/.skillset/receipts/<name>
```

Publication hard-links the symlink object itself from its receipt into the
install directory. This does not hard-link or copy skill contents. Ownership
requires the same symlink inode and target text. An unrelated replacement
pointing to exactly the same source is still a different object and is neither
adopted nor deleted. No JSON journal, source-prefix heuristic, or duplicate
source lockfile is needed.

The desired links hold a separate target for each install directory and name.
Switching between canonical and variant copies uses the existing relocation
path: remove the owned old link and publish the new target with its receipt.
Receipts keep the same format and require no migration.

The receipt is durable before exclusive publication. Removal rechecks ownership
immediately before unlinking, then persists the link removal before deleting
its receipt. Interrupted operations can converge from retained receipts without
promoting planned installations to ownership. An orphan receipt can be retired
or republished only while its install directory entry is absent. Changed links,
missing receipts and corrupt receipts fail closed.

Mutating runs serialize through `~/.agents/.skillset/lock` and repeat destination
and ownership preflight under the lock. Source and catalog validation runs once
before the lock. This supports serialized installs and detects ordinary
external replacement. It is not a cross-directory transaction and does not
promise protection against malicious same-user filesystem races. Do not run
other installers or mutate source checkouts concurrently. An I/O failure may
leave partial progress; rerun after repairing the cause.

## Persistence and restoration

The workstation persists `~/.agents` and `~/.claude` through an LXC rebuild,
but does not persist every directory under `~/.local/state`. Receipts therefore
live beside the install directories. Each install directory has its own receipt
directory, so publication does not cross their separate bind mounts.

Backup and restore must preserve the hard-link relationship between receipts
and the symlinks in install directories. Copy each complete harness tree with
`cp -a` or an equivalent hard-link-preserving backup. Copying the receipt and
install directory trees independently can lose that relationship. After restore, run `--check`.
If ownership evidence is lost, inspect and retire affected entries explicitly;
the reconciler never recreates ownership from a matching path or target.

## Identity and supported boundaries

The catalog and reconciler require no YAML dependency. Only the selector uses
PyYAML, to read display frontmatter. The first non-comment frontmatter field must
be a plain `name: skill-name` scalar. Later top-level keys must be plain and
unique. Block descriptions and nested metadata are supported, but continuation
of the name, quoted/escaped keys, explicit keys, merges and duplicate fields
fail closed. This is a restricted identity reader, not a general YAML validator.
Names must match their containing directory, use 1-64 lowercase ASCII letters,
digits or hyphens, and have no leading, trailing or consecutive hyphens.
`synced` is rejected because Claude Code reserves that local directory name.

Collision inspection includes regular and symlinked skill directories directly
inside `~/.agents/skills` and `~/.claude/skills`, including frontmatter
identity under a differently named folder. Unrelated files without skill
metadata are left alone. Ambiguous existing skill metadata fails safely rather
than hiding a possible collision. Project skills, plugins, built-ins and cloud
skill stores are outside this directory-level check.

An install rejects linked worktrees, symlinked destination/state parents,
uncommitted tracked superproject files (including staged gitlinks), missing or
wrong-revision sources, dirty sources, and ignored/untracked selected content.
Nested source submodules are deliberately unsupported for now; rejecting them
is smaller and safer than claiming incomplete recursive validation.

Install into the real install directories only from the live checkout, a
primary clone. Git worktrees remain useful for other development, but cannot
install. Links remain live views of their source checkout: validation proves
committed state at that moment, not immutable execution contents. Prepare source updates in a
separate clone and pull the published skillset commit into the live checkout.
Historical reconstruction also depends on the referenced Git objects remaining
available; Git pointers cannot preserve a deleted remote repository by themselves.

## What survives from the old installer

The new CLI preserves the old installer's useful behavioral boundaries: both
install directories, preflight conflicts, unrelated-entry preservation,
read-only checking, stale cleanup and primary-checkout safeguards. Tests adapt
those behaviors using real Git and filesystem fixtures, then add multi-source,
historical and interruption coverage.

The old source-owned installer and its tests remain intact for that repository;
skillset never invokes them. Its discover-everything policy, prefix-based stale
ownership and automatic exclusions are not used by the aggregate. No Broodling
integration, background updater or general package-management layer is added.

## Reference semantics

- [Agent Skills specification](https://agentskills.io/specification)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Git submodule](https://git-scm.com/docs/git-submodule)
- [Git environment controls](https://git-scm.com/docs/git)
