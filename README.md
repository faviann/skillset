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
`faviann/skills` fork. Its 31 selected skills preserve the previous effective
set, without inheriting an open-ended include-all rule.

The second source is `b1rdmania/claude-plain-english-skill` under
`sources/b1rdmania/claude-plain-english-skill`, pinned at v0.6.1. It selects
`plain-english` and `simple-english`. The repository-root `SKILL.md` copy, the
plugin manifests and the output style are not selected.

## Bootstrap a new installation

```bash
git clone https://github.com/faviann/skillset.git ~/repos/skillset
cd ~/repos/skillset
git submodule update --init --recursive --checkout
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

Reconciliation is offline. It requires committed configuration and initialized,
clean sources at their pinned commits. It exposes selected directories through
symlinks in `~/.agents/skills` and `~/.claude/skills`, retaining exact ownership
receipts beside each consumer directory. It never adopts existing entries.

**Existing workstation:** migrate the legacy author-repository links and switch
the dotfiles invocation before using this as the live installer. This repository
has not changed the workstation's existing links or its dotfiles hook. See the
[workflow and migration guide](docs/workflow.md).

See [architecture and safety boundaries](docs/architecture.md) for ownership,
identity, persistence, worktrees, and reproducibility decisions.

## Validation

```bash
python3 scripts/test-reconcile-skills.py
```

Tests use temporary homes and real local Git repositories. CI also initializes
the committed source configuration and reconciles it twice in a temporary home.
The validated platform is Linux with Python 3.13 and Git 2.47; there are no
third-party Python dependencies.
