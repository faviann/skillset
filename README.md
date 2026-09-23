# skillset

`skillset` records which Git-backed skill sources and individual skills are
deployed. Skill contents stay in their source repositories, pinned as
submodules. The committed [`skills.txt`](skills.txt) is the explicit effective
selection; adding a source alone installs nothing.

Start with the [architecture](docs/architecture.md) and
[workflow and migration guide](docs/workflow.md). After cloning, explicitly
initialize the pinned sources, then reconcile:

```bash
git submodule update --init --recursive
scripts/reconcile-skills.sh
scripts/reconcile-skills.sh --check
```

Reconciliation is local and offline. It requires a committed, clean skillset
checkout and initialized source checkouts at their exact pinned commits. It
links selected skill directories into `~/.agents/skills` and `~/.claude/skills`.
