# Workflow and migration

Two kinds of change reach the installed skills. Selection changes happen in the
live checkout through `select-skills`. Source pin changes happen in a separate
clone, are published, and are then pulled into the live checkout.

## Set up a machine

Clone skillset to the path that becomes the live checkout, then run setup:

```bash
git clone https://github.com/faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
./setup.sh
```

Setup checks for git, Python 3.11+ and uv. If uv is missing, it asks before using
the official installer; Enter or EOF declines and stops setup. It initializes sources at their
pins, prepares locked selector dependencies, and links `select-skills` and its
lockfile in `~/.local/bin`. It warns if that directory is missing from PATH.
With terminal input and output it opens the selector; otherwise it prints
`Ready. Run: select-skills`. Setup installs no skills.

In the live checkout, rerun `./setup.sh` whenever a source is missing or at the wrong commit, for
example after a pull that moved a pin. An install never fetches, pulls,
initializes, or advances sources. It requires the skillset's tracked files to
be committed and each initialized source to be clean and at the gitlink commit.

## Select skills

Select skills in the live checkout, on its branch, by running `select-skills`
from any directory. Space toggles the highlighted skill; Enter does the same in
the skills column. Clicking a circle toggles it, and clicking a name highlights
it. Selecting a name from another source asks before replacing its current
selection.

Ctrl+S reviews additions and removals against the last commit. **Save and install**
is the default: it writes `skills.txt`, closes the selector, commits only that
file if needed, and installs in your terminal. When the branch is ahead of its
upstream, it reminds you to run `git push`; the selector never pushes. Install
failures keep the commit, and commit failures keep the saved file without
installing. This choice is disabled with a reason in linked worktrees, on a
detached HEAD, during a merge or rebase, when other tracked changes are uncommitted,
when untracked files exist under `scripts/`, or while selected entries remain
under **Not in catalog**.

The title shows **not installed** when `skills.txt` differs from HEAD or a read-only
installation check finds links to update or fails. Ctrl+S then offers **Install** even with
no unsaved changes; it preserves the saved file and commits only if needed. A
failed check appears on the message line and disables Install with its reason.
After an install failure, run the skill selector again and choose Install.

**Save only** rewrites `skills.txt` in sorted order with its standard header and
stays open. It becomes the default when Save and install is unavailable.
**Keep editing** returns to the selection. The title counts unsaved changes
relative to the last loaded or saved file. If the file changed on disk, saving
asks whether to **Reload** it and discard unsaved changes or **Overwrite** it
with the current selection. Esc asks before discarding unsaved
changes. Entries that no longer resolve appear first under **Not in catalog**
with their reasons; deselect them to remove them, or leave them to preserve them
on save. Duplicate selected names must be resolved before saving.

You can also edit `skills.txt` by hand. Each selection line is
`owner/repo:name`: `owner/repo` is the source's folder under `sources/`,
and `name` is the skill's frontmatter name. Neither part may contain `:`.
For example, `faviann/agent-skills:publish-artifact` selects that named skill
from `sources/faviann/agent-skills`. Blank lines and lines beginning with `#`
are ignored. Directory-path selections are rejected; there is no conversion
from the old format.

The catalog includes valid tracked skills in every source layout, including
hidden folders, except beneath declared variant trees. Plugin manifests do not
control discovery. Names come from frontmatter and must match their parent
directories. Invalid skills are excluded from the catalog and retain
a validation reason. A repeated valid canonical name within one source stops
every install and `--check`, even when neither copy is selected. Selecting the
same name from different sources also stops the install before making changes.
Aliases are unsupported because a link name cannot safely change a skill's
frontmatter or harness identity.

## Update sources

Source pins change in a separate clone of skillset, never in the live checkout:
installed links expose the live checkout's sources directly. Make, commit and
publish skill edits in the source's own repository first. Then make the
separate clone:

```bash
git clone --recurse-submodules https://github.com/faviann/skillset.git ~/repos/skillset-update
cd ~/repos/skillset-update
```

Don't run `./setup.sh` in this clone; it would point `select-skills` at it, and
an install from there would link your skills into it. Add, advance or remove
the source:

```bash
# Add a source
git submodule add https://github.com/owner/repo.git sources/owner/repo
# Or advance an existing one to a reviewed, published commit
git -C sources/owner/repo fetch origin
git -C sources/owner/repo checkout --detach <reviewed-commit>
```

To remove a source, remove its selected lines from `skills.txt` and its
`sources.toml` entry, then remove the submodule.

In the same change, refresh `sources.toml`. Declare each source's harness
variant directories under `["owner/repo".variants]` using normalized paths
relative to that source. The supported keys are `claude-code`, `codex`, `pi`, and
`opencode`; each path must identify an existing tracked directory without
traversing symlinks. An agent can usually infer the entry from the upstream
layout, such as a `dist/codex` variant tree. These declarations exclude variant
copies from canonical discovery and choose which copies each install directory
receives through the
[fixed harness precedence](architecture.md#authority-and-committed-inputs).
A source without variants needs no entry. Keep `sources.toml` tracked, even if
it is empty.

If the new source commit removes or renames selected skills, update
`skills.txt` in the same commit. Moving a skill within its source while keeping
its frontmatter name leaves its selection valid; the install relinks it at the
new location. Commit `.gitmodules`, the gitlinks, `sources.toml` and any
selection changes together:

```bash
git add .gitmodules sources/owner/repo sources.toml skills.txt
git commit -m "Update owner/repo source pin"
```

Then validate the commit with an install into a throwaway home:

```bash
HOME="$(mktemp -d)" scripts/reconcile-skills.sh
```

This runs the same validation as `--check`, including repeated names that no
declared variant tree explains, invalid variant copies, and selections that no
longer resolve. Plain `--check` here would compare against your real install
directories, whose links point into the live checkout, and report them as
stale. If validation fails, fix the cause and amend the commit.

Publish the new source commit before publishing the skillset commit, so a fresh
clone can obtain it.

Then take the published update in the live checkout:

```bash
cd ~/repos/skillset
git pull
./setup.sh
```

Setup restores sources to the new pins and opens the selector. If anything
appears under **Not in catalog**, deselect it first. Then, if the title shows
**not installed**, press Ctrl+S and choose **Save and install**, or **Install**
when nothing is unsaved.

## Install and provenance

Run `scripts/reconcile-skills.sh` to install and
`scripts/reconcile-skills.sh --check` for a read-only check. The skillset
commit SHA identifies the selection and source pins used for the install.
It does not make linked source contents immutable: another process can modify
a checkout after the clean-state check, changing what the symlink exposes. Run
`--check` when you need to confirm the current committed and clean pinned state.

## Switch the workstation hook

The current dotfiles hook still invokes `~/repos/skills/scripts/reconcile-skills.sh`.
Change its checkout, URL, and installer variables to the skillset repository,
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

For an existing skillset checkout, the hook only installs; it does not pull
the superproject or initialize/advance sources implicitly. If the checkout
exists but has missing sources, run `./setup.sh` before applying the hook.
Continue withholding `BW_SESSION` from the installer as the current hook does.

Before switching, inspect entries in `~/.agents/skills` and `~/.claude/skills`
that the old installer created from `~/repos/skills`. Skillset does not adopt
those links: remove only the entries you have verified belong to the old
installer, then install. Keep unrelated local entries. Do not
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
Python packages. The code requires Python 3.11 or newer and Git with
`GIT_NO_LAZY_FETCH` support. Publication uses
hard links to symlink objects and directory `fsync`, so receipt and skill
directories must support these operations. Backups must preserve the hard links
between receipts and install directories (`cp -a` or equivalent). Since live
symlinks expose source checkout edits immediately, edit sources only in a
separate clone, as described in [Update sources](#update-sources).
