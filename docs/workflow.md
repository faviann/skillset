# Workflow and migration

## Clone and bootstrap

Clone the superproject, then explicitly initialize its pinned sources:

```bash
git clone https://github.com/faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
git submodule update --init --recursive --checkout
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

If a clone already exists but a source is uninitialized, run the same explicit
`git submodule update --init --recursive --checkout` command. Reconciliation itself never
fetches, pulls, initializes, or advances sources. It requires the skillset's
tracked files to be committed and each initialized source to be clean and at
the gitlink commit.

## Select skills

In a non-live authoring clone of skillset, add a source and discover its candidates:

```bash
git submodule add https://github.com/owner/repo.git sources/owner/repo
find sources/owner/repo -type f -name SKILL.md
# Add only the desired skill-directory paths to skills.txt.
git add .gitmodules sources/owner/repo skills.txt
git commit -m "Select skills from owner/repo"
```

Each non-comment line in `skills.txt` is a normalized checkout-relative path
to a selected skill directory containing `SKILL.md`. Commit `.gitmodules`,
the new gitlink, and the selection together. To remove a skill, delete its
line and commit. To remove a whole source, first remove its selected lines,
then remove the submodule and commit both changes. A later reconciliation
cleans only links recorded as owned, including dangling links to removed
sources.

Names come from frontmatter and must match their parent directories. If two
selected skills have the same name, deployment stops before making changes.
Aliases are unsupported because a link name cannot safely change a skill's
frontmatter or harness identity.

## Deliberately update a source

Make, commit and publish skill edits in the source's authoring repository.
Then, in a non-live authoring clone of skillset, check out the reviewed source
commit and record its gitlink:

```bash
git -C sources/owner/repo fetch origin
git -C sources/owner/repo checkout --detach <reviewed-commit>
git add sources/owner/repo
git commit -m "Update owner/repo source pin"
```

If the new source commit adds, removes, or renames skills, update `skills.txt`
in the same skillset commit. A source-only commit does not change the effective
selection. Publish the new source commit before publishing the skillset pin,
so a fresh clone can obtain it.

## Deployment and provenance

Run `scripts/reconcile-skills.sh` to converge and
`scripts/reconcile-skills.sh --check` for a read-only check. The skillset
commit SHA identifies the selection and source pins used for reconciliation.
It does not make linked source contents immutable: another process can modify
a checkout after the reconciler's clean-state check, changing what the symlink
exposes. Run `--check` when you need to confirm the current committed and clean
pinned state.

After reviewing and publishing a skillset commit, switch the dedicated deployment
clone explicitly. Do not make these source changes while agents are relying on
its live skill links:

```bash
cd ~/repos/skillset
git fetch origin
git checkout --detach <reviewed-skillset-commit>
git submodule sync --recursive
git submodule update --init --recursive --checkout
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

These Git commands are explicit deployment preparation, not reconciliation.
They do not replace or clean local changes forcibly. Resolve dirty checkouts
before switching; do not use automatic resets as a repair mechanism.

## Switch the workstation hook

The current dotfiles hook still invokes `~/repos/skills/scripts/reconcile-skills.sh`.
Change its checkout, URL, and reconciler variables to the skillset repository,
then use this bootstrap behavior:

```bash
skillset_repo="$HOME/repos/skillset"
skillset_url="https://github.com/faviann/skillset.git"
if [[ -e "$skillset_repo" || -L "$skillset_repo" ]]; then
  checkout_root="$(git -C "$skillset_repo" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ "$checkout_root" != "$skillset_repo" ]]; then
    printf 'error: skillset path is not a Git checkout: %s\n' "$skillset_repo" >&2
    exit 1
  fi
else
  mkdir -p "$(dirname "$skillset_repo")"
  git clone "$skillset_url" "$skillset_repo"
  git -C "$skillset_repo" submodule update --init --recursive --checkout
fi
env -u BW_SESSION "$skillset_repo/scripts/reconcile-skills.sh"
```

For an existing skillset checkout, the hook only invokes reconciliation; it
does not pull the superproject or initialize/advance sources implicitly. If
the checkout exists but has missing submodules, initialize them explicitly
before applying the hook. Continue withholding `BW_SESSION` from the
reconciler as the current hook does.

Before switching, inspect entries in `~/.agents/skills` and `~/.claude/skills`
that the old installer created from `~/repos/skills`. Skillset does not adopt
those links: remove only the entries you have verified belong to the old
installer, then run the new reconciler. Keep unrelated local entries. Do not
delete links by basename alone. Receipts are created only for links skillset
itself publishes; they have no authority over legacy entries.

List current symlinks for review with:

```bash
find ~/.agents/skills ~/.claude/skills -mindepth 1 -maxdepth 1 -type l \
  -printf '%p -> %l\n'
```

## Historical reconstruction

To reconstruct an older configuration, check out the desired skillset commit
and explicitly initialize/update its gitlinks to that commit:

```bash
git checkout --detach <skillset-commit>
git submodule sync --recursive
git submodule update --init --recursive --checkout
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

No separate source-SHA lockfile is needed; the historical superproject tree
contains the selection and pinned gitlinks. Always pass `--checkout` explicitly
because local `submodule.update` configuration can otherwise select merge or
rebase.

## Runtime and source editing

Validated runtime: Linux, Python 3.13.5 and Git 2.47.3, with no third-party
Python packages. The code requires Python 3.10 or newer and Git with
`GIT_NO_LAZY_FETCH` support. Publication uses
hard links to symlink objects and directory `fsync`, so receipt and skill
directories must support these operations. Backups must preserve receipt-to-
consumer hard-link relationships (`cp -a` or equivalent). Since live symlinks
expose source checkout edits immediately, prepare deliberate updates in a
separate non-live clone, then advance and commit the reviewed gitlink.
