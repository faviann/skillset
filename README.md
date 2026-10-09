# skillset

Skillset is the versioned definition and eventual build system for reproducible
agent harness environments.

Today it pins skill sources and projects an explicit selection of skills onto
local harnesses. Longer term, it is intended to assemble reproducible harness
packages — skills, hooks, harness-specific adapters and configuration — that can
be materialized consistently for interactive use or consumed by execution
systems such as Broodling and ZeroShot.

Skill contents stay in independent source repositories. Git submodule pointers
pin their exact commits; [skills.txt](skills.txt) explicitly selects the skills
to expose. Adding a source or adding a skill upstream does not install it.

Each source lives under `sources/<owner>/<repo>`, named after its canonical
repository, and its gitlink is its pin. `scripts/update-sources.sh` moves pins
to the tip of each source's tracked branch, one source at a time, after you
review the change; see [Update sources](docs/workflow.md#update-sources). The
sources today:

- `mattpocock/skills`, fetched from the maintained `faviann/skills-mattpocock`
  fork. The fork was reset to upstream `main` on 2026-10-02; its earlier history
  is kept under the tag `archive/pre-reset-2026-10`.
- `b1rdmania/claude-plain-english-skill`.
- `rampstackco/claude-skills`, whose release tags trail its default branch, so
  the pin follows the branch.
- `michael-denyer/pstack-claude`, with skills under `plugins/pstack/skills`.
  The same skills are installed on this workstation as the `pstack` Claude Code
  plugin, which also carries the agents and hook they depend on, so selecting
  them here would duplicate that plugin.
- `humanlayer/skills`, with skills under `plugins/*/skills`. Skill evaluation
  is tracked in [#11](https://github.com/faviann/skillset/issues/11).
- `faviann/agent-skills`, the repository for first-party skills authored by
  faviann. It includes `publish-artifact`, which moved there with its history
  from the `mattpocock/skills` fork. Author new first-party skills in that
  repository, not in skillset.

## Get started

```bash
git clone https://github.com/faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
./setup.sh
```

This clone becomes the live checkout: installed skill links point into it. Setup
requires git and Python 3.11+, and offers to install uv if it is missing (Enter
or EOF declines and stops setup). It initializes sources at their pins, prepares
the selector's locked dependencies, and links `select-skills` in `~/.local/bin`.
Add that directory to PATH if setup warns. In a terminal, setup opens the
selector; otherwise, run `select-skills` when ready. Setup installs no skills.
Rerun `./setup.sh` to restore missing or stale sources.

In the selector, choose skills, then press Ctrl+S and choose **Save and
install**. That commits `skills.txt` in the live checkout and links the selected
skills into the install directories `~/.agents/skills` and `~/.claude/skills`.
To move one source's pin, highlight it in the selector and press Ctrl+U. To
move every pin, run `scripts/update-sources.sh` from the live checkout. See the
[workflow guide](docs/workflow.md#update-sources).

Installing is offline. It requires committed configuration and initialized,
clean sources at their pinned commits, and keeps an ownership receipt for each
link beside its install directory. It never adopts existing entries.

**Codex names:** Codex lists a linked skill that sits under a plugin manifest as
`<plugin>:<name>`, such as `pstack:tdd`, typed as `$pstack:tdd`. Every source
except `faviann/agent-skills` ships plugin manifests, so this affects their
skills. Selecting a variant does not avoid it.

**Existing workstation:** migrate the legacy links from `~/repos/skills` by hand
and switch the dotfiles invocation before using this as the live installer;
skillset never removes links it did not create. If you installed with an earlier
skillset version, `skills.txt` is now empty: path selections are not converted,
and the next install removes the links skillset made for them. Select skills
again with `select-skills`. This repository has not changed the workstation's
existing links or its dotfiles hook. See the [workflow and migration
guide](docs/workflow.md#switch-the-workstation-hook).

See [architecture and safety boundaries](docs/architecture.md) for ownership,
identity, persistence, worktrees, and reproducibility decisions.

## Validation

```bash
tests/run.sh
# Or run a single group:
tests/run.sh stdlib
tests/run.sh selector
```

Tests use temporary homes and real local Git repositories. CI also initializes
the committed source configuration and installs it twice in a temporary home.
The code requires Python 3.11 or newer. The validated platform is Linux with
Python 3.13 and Git 2.47. The selector group needs uv and uses the selector's
locked dependencies; the stdlib group and installer need no third-party Python
packages. With no argument, the test runner runs both groups.
