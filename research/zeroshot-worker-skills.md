# How a Zeroshot worker finds its skills inside a target

Research for [#88](https://github.com/faviann/skillset/issues/88), part of the map
[#87](https://github.com/faviann/skillset/issues/87). Researched 2026-10-12.

## Answer

On a hosted Zeroshot target, every agent session gets a fresh, empty, private `HOME` under the
run's storage. Zeroshot sets `HOME` (and `CODEX_HOME` for Codex) to that directory and clears the
rest of the environment. So `~/.claude/skills`, `~/.agents/skills` and anything in the image's
`/home/node` are never seen by a worker. A run cannot redirect them either, because `HOME`,
`CODEX_HOME` and `CLAUDE_CONFIG_DIR` are reserved variable names.

A worker can see skills from two places only:

1. **The repository checkout** (the agent's working directory): `.claude/skills` for Claude Code,
   `.agents/skills` for Codex.
2. **System-wide directories baked into the image**, which do not depend on `HOME`:
   `/etc/claude-code/.claude/skills/<name>/` for Claude Code (the managed settings directory) and
   `/etc/codex/skills/<name>/` for Codex (Admin scope).

For a profile written into a directory, option 2 is the one that fits: the image build copies the
profile's Claude tree to `/etc/claude-code/.claude/skills` and its Agents tree to
`/etc/codex/skills`. Reviewers and implementers see exactly the same skills.

## Sources and versions

| Source | Revision |
| --- | --- |
| `the-open-engine/zeroshot` (local `/home/faviann/repos/zeroshot`) | `511a586` (2026-10-08) |
| `faviann/zeroshot-environments` (local) | `f88d450` (2026-10-09) |
| `faviann/broodling` (local) | `4bd7abb` (2026-10-08) |
| Harnesses in the `dotnet` image | Claude Code 2.1.276, Codex 0.155.0 (`environments/dotnet/environment.json`) |
| Codex source | tag `rust-v0.155.0` of `openai/codex`, below `G/` = `https://github.com/openai/codex/blob/rust-v0.155.0/codex-rs/` |
| Claude Code docs | `https://code.claude.com/docs/en/…`, live pages (they describe roughly 2.1.29x, newer than the image) |

Zeroshot paths below are relative to the zeroshot repo root; `src/` means `zeroshot/src/`.

## 1. What a hosted worker's process looks like

**Two kinds of target.** The local target (no `--target`) runs the harness as the current user
with the user's home, so a workstation's skillset install works there
(`docs/concepts/targets.md:8-23`, `docs/concepts/runtimes-and-connections.md:81-84`). Everything
below is about hosted targets, which is what a `zeroshot-environments` image runs.

**The image's own home is not the worker's home.**

- The `dotnet` image sets `HOME=/home/node` and `CODEX_HOME=/home/node/.codex` for the server
  process and empties `/home/node` (`zeroshot-environments/environments/dotnet/Dockerfile:28-37`).
- Broodling's entrypoint refuses to initialize unless `/state` and `/home/node` are empty, and
  refuses any other home path (`broodling/deployment/direct-target-entrypoint.sh:41-44, 83-84`).
  It writes its own origin binding into `/home/node/.config/broodling/origin` (line 51).
- `/home/node` is therefore the native server's home. Writing skills there would break
  initialization and would still not reach a worker.

**Each agent session gets its own empty home.**

- Per run, the target lays out `<storage>/runs/<sha256>/workspace` and `<storage>/runs/<sha256>/runtime`
  (`src/native_v2_hosting/allocator.rs:118-126, 1433-1438`). The storage root is the
  `--storage` argument, `/state` under Broodling (`src/native_v2_target/serve.rs:140-143`).
- Each agent session's home is a leaf under `runtime/`: `writer-node-instance-N`,
  `writer-execution-N`, `verifier-node-instance-N` or `verifier-execution-N`
  (`src/execution/process.rs:114-127`). It is created on the session's first turn as a private
  directory owned by the session identity (`src/execution/process.rs:201-209, 345-358`,
  `src/native_v2_capsule/provider_process/filesystem.rs:70-94`). The runtime root itself is not
  writable by provider processes (`src/execution/process.rs:201-204`).
- Claude: `HOME` and `TMPDIR` are set to that leaf (`src/native_v2_claude.rs:261-284`). Only
  `HOME`, `LANG`, `LC_ALL`, `PATH`, `TERM`, `TMPDIR` and `ZEROSHOT_TOOLS` may come from the base
  environment (`src/native_v2_claude.rs:39-47`).
- Codex: `HOME` and `CODEX_HOME` are both set to that leaf (`src/native_v2_codex.rs:171-194`,
  `src/native_v2_codex/command.rs:19-38`).
- Every hosted child starts from `env_clear()` plus that explicit map
  (`src/execution/process/spawn_recovery.rs:106-124`), so image `ENV` lines do not reach agents.
- The working directory is the run's checkout (`src/native_v2_claude.rs:249-256`,
  `src/native_v2_codex.rs:160-168`).
- The docs say the same in prose: "Hosted runs use private homes and do not inherit the caller's
  `USER` or user configuration" (`docs/concepts/runtimes-and-connections.md:83-84`); "Setup,
  startup, and agent sessions have separate home directories"
  (`docs/guides/runtime-environments.md:104-106`).

**The command line adds nothing that changes skill loading.**

- Claude runs as `claude --print --input-format text --output-format stream-json --verbose
  --include-partial-messages --model … --json-schema …` plus `--effort`/`--resume`
  (`src/native_v2_claude/command.rs:28-52`), and on hosted targets always
  `--dangerously-skip-permissions` (`src/native_v2_claude/permissions.rs:28-40`). No `--bare`,
  `--setting-sources`, `--disable-slash-commands` or `--plugin-dir`.
- Codex runs as `codex exec --json --model … --skip-git-repo-check --output-schema …` plus provider
  `--config` flags (`src/native_v2_codex.rs:136-150`, `src/native_v2_codex/command.rs:102-161`).
  A test asserts `--ignore-user-config` and `--ignore-rules` are never passed
  (`src/native_v2_codex/tests.rs:434-443`).
- The target has no operator knob for extra harness arguments or environment: `target serve`
  builds its hosting config from defaults plus `--storage` (`src/native_v2_target/serve.rs:140-144`),
  and the defaults are an empty Claude prefix-argument list and an empty Claude environment
  (`src/native_v2_hosting.rs:330-346`).

## 2. Which directories each harness reads there

Read against the session above (`HOME` = empty private leaf, cwd = checkout).

### Claude Code

Skill locations (https://code.claude.com/docs/en/skills, "Choose where skills load"):

| Location | Path | Reached in a hosted session? |
| --- | --- | --- |
| Enterprise | `.claude/skills/<name>/SKILL.md` in the managed settings directory, `/etc/claude-code/.claude/skills/<name>/` on Linux | **Yes**, if the image ships it (no `HOME` involved) |
| Personal | `~/.claude/skills/<name>/SKILL.md`, relocated by `CLAUDE_CONFIG_DIR` (https://code.claude.com/docs/en/claude-directory) | No: `~` is the empty session home, and `CLAUDE_CONFIG_DIR` is reserved |
| Project | `.claude/skills/` in the start directory and every parent up to the repo root | **Yes**, if the repository has them |
| Nested | `<subdir>/.claude/skills/`, loaded when Claude first touches a file there | Yes, same as project |
| `--add-dir` | `.claude/skills/` of an added directory | No: Zeroshot passes no `--add-dir` |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | See section 4 |

- `claude -p` loads the same context as an interactive session unless `--bare` is given
  (https://code.claude.com/docs/en/headless). Zeroshot passes neither `--bare` nor
  `--setting-sources`, so user, project and enterprise skills all load.
- A `-p` run never shows the trust dialog, and plain project skills load in an untrusted folder
  (https://code.claude.com/docs/en/permissions, "What runs before you trust a folder").
- Enterprise, personal and project entries may be symlinks to directories
  (skills page, "Symlinked folders"). Enterprise wins over personal, personal over project.
- Not verified: the enterprise location is documented on today's pages; I did not confirm that
  2.1.276, the version in the image, already reads it.

### Codex

Skill roots, from `G/ext/skills/src/host_roots.rs`:

| Root | Scope | Reached in a hosted session? |
| --- | --- | --- |
| `$CODEX_HOME/skills` (deprecated user location) | User | No: `CODEX_HOME` is the empty session home (L95-101) |
| `$HOME/.agents/skills`, via `dirs::home_dir()` (reads `$HOME`) | User | No: `HOME` is the empty session home (L103-108) |
| `$CODEX_HOME/skills/.system` (bundled skills Codex writes itself) | System | Codex's own bundled skills only (L110-113) |
| `/etc/codex/skills` (folder of `/etc/codex/config.toml`) | Admin | **Yes**, if the image ships it (L115-120) |
| `.codex/skills` for each project `.codex` folder from repo root to cwd | Repo | Yes, if the repository has them (L86-94) |
| `.agents/skills` in every directory from the repo root (nearest `.git`) down to cwd | Repo | **Yes**, if the repository has them (L137-185) |

- The System layer is pushed even when `/etc/codex/config.toml` does not exist; a missing file
  becomes an empty table (`G/config/src/loader/mod.rs:297-314, 631-633`). So
  `/etc/codex/skills` needs no `config.toml` beside it.
- Directory symlinks are followed for User, Repo and Admin scopes; a `SKILL.md` that is itself a
  file symlink is skipped (`G/ext/skills/src/loader/host.rs:160-163`). Hidden directories are
  pruned and the scan depth is capped at 6 (`G/ext/skills/src/loader/mod.rs:19, 31-32`).
- Repo skill discovery does not depend on project trust (`G/ext/skills/src/host_roots.rs:137-185`;
  test `layer_roots_preserve_scope_precedence_and_disabled_projects`).
- `codex exec` runs through the same in-process app server (`G/exec/src/lib.rs:18-23`), and I
  found no exec-specific skip in the skills extension. That is a reading of source, not a
  documented guarantee.
- Codex never reads `.claude/skills` at runtime; it appears only in a one-time migration
  (`G/external-agent-migration/src/service.rs:700-730`).

## 3. What Zeroshot puts into the target for a worker

- **Provider config:** credentials and provider selection only, as environment variables from
  declared connections (`src/native_v2_claude/command.rs:144-177`,
  `src/native_v2_codex/command.rs:41-161`). No harness settings files are written into the
  session home.
- **Home:** the private per-session directory above, created empty. Nothing is copied into it.
- **Workspace:** the run's checkout at `runs/<sha256>/workspace`, shared by every node of the run
  (`docker/zeroshot-target/README.md:71-79`, `docs/concepts/targets.md:79-81`).
- **Tools:** `$ZEROSHOT_TOOLS` (`runs/<sha256>/tools`, with `bin/` on every agent's `PATH`) is the
  one documented place for run-wide extras (`docs/guides/runtime-environments.md:100-106`,
  `src/native_v2_hosting/environment.rs:282-288`). Harnesses do not look for skills there.

**Hooks for extra files.** The only supported hook is the submission's `environment`:

- `setup` runs as root before checkout; `startup` runs as the workspace user in the checkout,
  before any agent; `variables` reach later commands (`docs/guides/runtime-environments.md:1-12,
  92-106`, `docker/zeroshot-target/README.md:69-79`).
- `variables` cannot set `HOME`, `PATH`, `TMPDIR`, `ZEROSHOT_TOOLS`, `CODEX_HOME`,
  `CLAUDE_CONFIG_DIR` or `COPILOT_HOME` (`crates/openengine-cluster-protocol/src/native_v2_run/environment.rs:55-85`).
- Hook homes are separate from session homes (`src/native_v2_hosting/environment.rs:290-308`), and
  session homes are created later, so no hook can seed `~/.claude` or `~/.agents`.
- `startup` could copy skills into the checkout's `.claude/skills` or `.agents/skills`. Delivery
  runs `git add --all` (`src/native_v2_delivery/git.rs:63`), so they would land in the PR unless
  excluded (for example in `.git/info/exclude`). Untested.
- Submissions are Broodling's to make. `zeroshot-environments` notes that excluding `setup` is
  Broodling's rule (`zeroshot-environments/README.md:73-74`), and Broodling #235 forbids
  "arbitrary repository installation scripts" and lets no caller pick setup commands. So a hook
  is not a path skillset can count on.

The image itself is the supported carrier: `zeroshot-environments` builds each target from a
recipe, and Broodling only selects among approved images (Broodling #234, #235;
`zeroshot-environments/README.md:1-7`).

## 4. Plugins

Plain skill directories are the reliable unit. Plugins are not reachable through the paths
Zeroshot uses today:

- **Claude Code.** Plugins are declared in `enabledPlugins` and fetched under
  `~/.claude/plugins/`, or loaded per session with `--plugin-dir`, `--plugin-url` or
  `CLAUDE_CODE_PLUGIN_DIRS` (https://code.claude.com/docs/en/plugins/loading). In a hosted
  session `~` is empty and Zeroshot adds no flag. `CLAUDE_CODE_PLUGIN_DIRS` needs 2.1.280 or
  later (https://code.claude.com/docs/en/env-vars); the image has 2.1.276. Possible but untested:
  image-level managed settings (`/etc/claude-code/managed-settings.json`) with
  `extraKnownMarketplaces` and `enabledPlugins`, or a `CLAUDE_CODE_PLUGIN_SEED_DIR` set by a run
  variable (https://code.claude.com/docs/en/plugins/org). Both are harness settings, which the map
  puts out of scope.
- **Codex.** Plugins are on by default, installed into `$CODEX_HOME/plugins/cache` and enabled by
  `[plugins."name@marketplace"]` in config (`G/core-plugins/src/store.rs:26`,
  `G/core-plugins/src/loader.rs:735-748`). `CODEX_HOME` is the empty session home, and there is no
  per-session plugin-directory flag.
- What a plugin adds over a bare skill: for Claude, agents, hooks, MCP, LSP, `bin/` and more
  (https://code.claude.com/docs/en/plugins-reference); for Codex, MCP servers, apps and hooks, but
  no agents (`G/core-plugins/src/manifest.rs:47-73`). Skills that depend on a plugin's agents or
  hooks (pstack, #79/#80 bindings) lose those parts in a target.

## 5. Reviewer versus implementer

No difference in skill discovery:

- One harness serves the whole graph: the run's `RuntimePlan` is Codex, Claude or Copilot, never
  per node (`src/native_v2_hosting/allocator.rs:668-725`, `src/native_v2_claude.rs:1-5`).
  Broodling's current asset runs every node, worker and verifiers alike, on Codex
  (`broodling/src/Broodling/execution-assets/software-change-pr-codex-gateway.json:1952-2022`).
- Workers and verifiers share the checkout with read-write access
  (`src/native_v2_capsule/provider_process.rs:40-47`, `docs/concepts/targets.md:78-81`).
- They differ only in which home leaf they get (`writer-*` or `verifier-*`,
  `src/native_v2_capsule/provider_process.rs:275-298`), and both leaves start empty; and in
  instructions Zeroshot adds to verifiers (`docs/concepts/targets.md:71-75`).
- Broodling selects one target per Attempt (#235), so every node in a run sees the same image.
  Different skills for reviewers and implementers would need different runs on different images.

## Implications for writing a profile into a directory

- Write real copies, not links. Image builds copy the directory, and symlinks into a skillset
  checkout would dangle. Both harnesses accept a directory per skill containing `SKILL.md`.
- Produce two trees, because the harnesses read different places and variants differ per harness:
  one for Claude Code (destined for `/etc/claude-code/.claude/skills/`) and one for Codex (destined
  for `/etc/codex/skills/`). The same Agents-style tree would also serve a repo's `.agents/skills`.
- Each tree is flat: `<tree>/<skill-name>/SKILL.md` plus the skill's files. Avoid dot-prefixed
  skill folders (Codex prunes hidden directories) and keep nesting under 6 levels.
- Never target `/home/node` or any `~` path. It must stay empty for initialization and is not the
  worker's home anyway.
- Files must be readable by non-root agent identities (directories `0755`, files `0644`) and need
  not be writable.
- One profile per image serves every worker and reviewer in a run.
- Plugin-only parts (agents, hooks, MCP) do not travel with a plain copy. A profile that selects
  such skills needs a decision, which the map already lists under "Not yet specified".

## Not verified

- No live run was made. Everything above is read from source and docs.
- Whether Claude Code 2.1.276 already reads `/etc/claude-code/.claude/skills`; the docs describe
  newer releases.
- That `codex exec` loads skills exactly like the interactive client (source reading only).
- Whether managed-settings plugins or a plugin seed directory work in a hosted session.
- Whether Broodling allows any `environment.startup` hook.
