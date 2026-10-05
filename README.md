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

The first source is the canonical upstream `mattpocock/skills`, represented
under `sources/mattpocock/skills` and currently fetched from the maintained
`faviann/skills-mattpocock` fork. The fork was reset to upstream `main` on
2026-10-02; its earlier history is kept under the tag
`archive/pre-reset-2026-10`.

The second source is `b1rdmania/claude-plain-english-skill` under
`sources/b1rdmania/claude-plain-english-skill`, pinned at v0.6.1.

The third source is `rampstackco/claude-skills` under
`sources/rampstackco/claude-skills`, pinned at commit 3d4510a from 2026-09-15.
Its last release tag, v1.2.0, is 67 commits older, so the pin follows the
default branch.

The fourth source is `michael-denyer/pstack-claude` under
`sources/michael-denyer/pstack-claude`, pinned at v0.9.45, with skills under
`plugins/pstack/skills`.
The same skills are installed on this workstation as the `pstack` Claude Code
plugin, which also carries the agents and hook they depend on, so selecting
them here would duplicate that plugin.

The fifth source is `humanlayer/skills` under `sources/humanlayer/skills`,
pinned at commit ca7c808 from 2026-09-17 and fetched directly from upstream.
Its skills live under `plugins/*/skills`. Skill evaluation is tracked in
[#11](https://github.com/faviann/skillset/issues/11).

The sixth source is `faviann/agent-skills` under
`sources/faviann/agent-skills`, the repository for first-party skills authored
by faviann. It includes `publish-artifact`, which moved there with its history
from the `mattpocock/skills` fork. Author new first-party skills in that
repository, not in skillset.

This upgrade empties `skills.txt`. Existing path selections are not converted,
and their links are removed on the next install; select skills again with
`select-skills`.

## Get started

```bash
git clone https://github.com/faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
./setup.sh
```

This clone becomes the live checkout: installed skill links point into it.
Setup requires git and Python 3.11+, and offers to install uv if it is missing
(Enter or EOF declines). It initializes sources at their pins, prepares the
selector's locked dependencies, and links `select-skills` in `~/.local/bin`.
Add that directory to PATH if setup warns. In a terminal, setup opens the
selector; otherwise, run `select-skills` when ready. Setup installs no skills.
Rerun `./setup.sh` to restore missing or stale sources.

Choose skills with `select-skills`, then press Ctrl+S and choose
**Save and install**. That commits `skills.txt` in the live checkout and links
the selected skills into the install directories `~/.agents/skills` and
`~/.claude/skills`. Source pin updates happen in a separate clone; see the
[workflow guide](docs/workflow.md).

Installing is offline. It requires committed configuration and initialized,
clean sources at their pinned commits, and keeps an ownership receipt for each
link beside its install directory. It never adopts existing entries.

**Codex names:** Codex lists a linked skill that sits under a plugin manifest as
`<plugin>:<name>`, such as `pstack:tdd`, typed as `$pstack:tdd`. Every source
except `faviann/agent-skills` ships plugin manifests, so this affects their
skills. Selecting a variant does not avoid it.

**Existing workstation:** migrate the legacy author-repository links and switch
the dotfiles invocation before using this as the live installer. This repository
has not changed the workstation's existing links or its dotfiles hook. See the
[workflow and migration guide](docs/workflow.md#switch-the-workstation-hook).

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
