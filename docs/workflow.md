# Workflow and migration

## Clone and bootstrap

Clone the superproject, then explicitly initialize its pinned sources:

```bash
git clone git@github.com:faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
git submodule update --init --recursive
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

If a clone already exists but a source is uninitialized, run the same explicit
`git submodule update --init --recursive` command. Reconciliation itself never
fetches, pulls, initializes, or advances sources. It requires the skillset's
tracked files to be committed and each initialized source to be clean and at
the gitlink commit.

## Select skills

Add a source, then select only the desired skill directories in `skills.txt`:

```bash
git submodule add git@github.com:owner/repo.git sources/owner/repo
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

Make and commit skill edits in the source's authoring repository. In skillset,
advance the submodule deliberately, review its new commit, and commit the
gitlink:

```bash
git -C sources/owner/repo fetch origin
git -C sources/owner/repo checkout --detach <reviewed-commit>
git add sources/owner/repo
git commit -m "Update owner/repo source pin"
```

If the new source commit adds, removes, or renames skills, update `skills.txt`
in the same skillset commit. A source-only commit does not change the effective
selection.

## Deployment and provenance

Run `scripts/reconcile-skills.sh` to converge and
`scripts/reconcile-skills.sh --check` for a read-only check. The skillset
commit SHA identifies the selection and source pins used for reconciliation.
It does not make linked source contents immutable: another process can modify
a checkout after the reconciler's clean-state check, changing what the symlink
exposes. Run `--check` when you need to confirm the current committed and clean
pinned state.

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
  git -C "$skillset_repo" submodule update --init --recursive
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
delete links by basename alone. The new ownership file is created only for
links skillset itself installs; it has no authority over legacy entries.

List current symlinks for review with:

```bash
find ~/.agents/skills ~/.claude/skills -mindepth 1 -maxdepth 1 -type l \
  -printf '%p -> %l\n'

To reconstruct an older configuration, check out the desired skillset commit
and explicitly initialize/update its gitlinks to that commit:

```bash
git checkout <skillset-commit>
git submodule update --init --recursive
scripts/reconcile-skills.sh --check
```

No separate source-SHA lockfile is needed; the historical superproject tree
contains the selection and pinned gitlinks.
