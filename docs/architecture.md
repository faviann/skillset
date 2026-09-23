# Architecture

## Authority and committed inputs

Source repositories own their skill contents. `faviann/skills` remains the
independent authoring repository. `faviann/skillset` owns only the aggregate
source configuration, explicit selection, and projection into consumers.

Each source is a Git submodule below `sources/<owner>/<repo>`. `.gitmodules`
records its URL and path; the superproject gitlink is the only source-commit
lock. `skills.txt` lists selected skill directories, not an inventory of every
available skill. Paths handle different source layouts without adapters or a
second source manifest. Discover candidates from `SKILL.md` and select their
paths; the reconciler derives names from frontmatter and checks the directory
name. A single skill at a source root is supported when its identity matches
that root directory. No content is copied and aliases are unsupported.

The initial 31 selections preserve the old effective set: exclude the
`in-progress`, `deprecated` and `node_modules` trees, plus
`migrate-to-shoehorn`, `obsidian-vault` and `work-on`. Those are initial
selection decisions, not inherited rules. New source skills remain unselected.
A selected directory must not contain additional `SKILL.md` files or directory
symlinks that would expose other skills implicitly.

## Reconciliation and ownership

The standard-library Python reconciler validates committed inputs, exact
source pins, source cleanliness, skill identities and both destinations before
making changes. It does not fetch, pull, initialize or advance sources. Git
lazy fetching is disabled too. `--check` performs the same validation without
creating a lock or writing state. A concurrent writer can cause a transient
check failure; this is not a globally atomic snapshot.

Each harness retains per-name symlink receipts:

```text
~/.agents/.skillset/receipts/<name>
~/.claude/.skillset/receipts/<name>
```

Publication hard-links the symlink object itself from its receipt into the
consumer directory. This does not hard-link or copy skill contents. Ownership
requires the same symlink inode and target text. An unrelated replacement
pointing to exactly the same source is still a different object and is neither
adopted nor deleted. No JSON journal, source-prefix heuristic, or duplicate
source lockfile is needed.

The receipt is durable before exclusive publication. Removal rechecks ownership
immediately before unlinking, then persists the consumer removal before deleting
its receipt. Interrupted operations can converge from retained receipts without
promoting planned installations to ownership. An orphan receipt can be retired
or republished only while its consumer entry is absent. Changed consumers,
missing receipts and corrupt receipts fail closed.

Mutating runs serialize through `~/.agents/.skillset/lock` and repeat preflight
under the lock. This supports serialized reconciliation and detects ordinary
external replacement. It is not a cross-directory transaction and does not
promise protection against malicious same-user filesystem races. Do not run
other installers or mutate source checkouts concurrently. An I/O failure may
leave partial progress; rerun after repairing the cause.

## Persistence and restoration

The workstation persists `~/.agents` and `~/.claude` through an LXC rebuild,
but does not persist every directory under `~/.local/state`. Receipts therefore
live beside the consumer trees. Each harness has its own receipt directory,
so publication does not cross the separate harness bind mounts.

Backup and restore must preserve the hard-link relationship between receipts
and consumer symlinks. Copy each complete harness tree with `cp -a` or an
equivalent hard-link-preserving backup. Copying the receipt and consumer trees
independently can lose that relationship. After restore, run `--check`.
If ownership evidence is lost, inspect and retire affected entries explicitly;
the reconciler never recreates ownership from a matching path or target.

## Identity and supported boundaries

No YAML dependency is required. The first non-comment frontmatter field must
be a plain `name: skill-name` scalar. Later top-level keys must be plain and
unique. Block descriptions and nested metadata are supported, but continuation
of the name, quoted/escaped keys, explicit keys, merges and duplicate fields
fail closed. This is a restricted identity reader, not a general YAML validator.
Names must match their selected directory, use 1-64 lowercase ASCII letters,
digits or hyphens, and have no leading, trailing or consecutive hyphens.
`synced` is rejected because Claude Code reserves that local directory name.

Collision inspection includes regular and symlinked skill directories directly
inside `~/.agents/skills` and `~/.claude/skills`, including frontmatter
identity under a differently named folder. Unrelated files without skill
metadata are left alone. Ambiguous existing skill metadata fails safely rather
than hiding a possible collision. Project skills, plugins, built-ins and cloud
skill stores are outside this directory-level check.

Deployment rejects linked worktrees, symlinked destination/state parents,
uncommitted tracked superproject files (including staged gitlinks), missing or
wrong-revision sources, dirty sources, and ignored/untracked selected content.
Nested source submodules are deliberately unsupported for now; rejecting them
is smaller and safer than claiming incomplete recursive validation.

Use a dedicated primary clone for deployment. Git worktrees remain useful for
other development, but cannot install into the shared user destinations. Links
remain live views of their source checkout: validation proves committed state
at that moment, not immutable execution contents. Prepare source updates in a
separate non-live clone and deploy the resulting reviewed skillset commit.
Historical reconstruction also depends on the referenced Git objects remaining
available; Git pointers cannot preserve a deleted remote repository by themselves.

## What survives from the old installer

The new CLI preserves the old installer's useful behavioral boundaries: both
consumer directories, preflight conflicts, unrelated-entry preservation,
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
