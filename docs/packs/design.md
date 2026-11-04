# Packs

Status: proposed. Design only; nothing here is built yet.

Skillset grows from "pinned skills linked into install directories" into
**packs**: versioned sets of skills, hooks and agents that a profile selects and
an install puts on a home. This document records the model, walks five
scenarios with their cost, drafts the ADRs and glossary changes, and gives a
validation plan that runs against the installed harnesses.

Evidence labels: **observed** means seen in a throwaway home on Claude Code
2.1.316 or Codex 0.166.0, and the check that reproduces it is named;
**documented** means stated by the harness's own docs; **estimate** means a
line count or effort guess. The numbers on what sources ship come from
[`inventory.py`](inventory.py), which reads only Git objects:
`python3 -I docs/packs/inventory.py .`.

## Summary

A pack is what a profile selects. A pack that carries only skills is what every
selection already is, and it keeps installing as one link per skill. A pack that
carries hooks or agents is a Claude Code plugin directory inside a pinned
source. It is declared once in `sources.toml`, selected with the same
`owner/repo:name` profile line as a skill, and installed as one receipt-owned
link, `~/.claude/skills/<pack>`. Claude Code loads that link in place as
`<pack>@skills-dir`, with its skills, agents and hooks, and skillset writes no
harness configuration (observed, P1 and S1). A pin bump that changes a hook or
agent gets its own accept in the per-source update review. Hooks and agents
reach Claude Code only; Codex, OpenCode and Pi keep plain skill links.
Instruction files stay with dotfiles.

The counterweight. This is about 275 lines of code and 220 of tests (estimate),
most of it in the catalog and the update review. It does not build a
`settings.json` writer, a generated plugin per profile, a pack store, hook
content hashes, Codex trust writes or a Zeroshot target writer. Each is
rejected or deferred below with the trigger that would bring it back.

## Pack model

### What a pack is

| Shape | Today | Declared by | Installs as |
|---|---|---|---|
| Skill pack: one skill directory | every selected skill | catalog discovery, unchanged | a link per install directory, unchanged |
| Plugin pack: a directory with `.claude-plugin/plugin.json` and `hooks/hooks.json` or `agents/` | `plugins/pstack` in `michael-denyer/pstack-claude`; `herdr` in `faviann/agent-skills` once it exists | `sources.toml` | one link in `~/.claude/skills` only |

A plugin pack is declared, never discovered. Eight of nine sources ship plugin
manifests, and most describe skills-only bundles whose skills profiles already
choose one by one. Discovery would fill the catalog with packs nobody selects.

A plugin pack must carry hooks or agents. A skills-only plugin directory is
refused with "select its skills instead". A pack link renames its skills to
`<pack>:<skill>` (observed in the design prototypes), and decision 2a keeps skills-only
selections under their own names.

A skill directory must not carry its own plugin manifest. Codex lists such a
directory as `herdr-playbook:herdr-playbook` instead of `herdr-playbook`
(observed, C1). Hooks that belong to a skill therefore live in a sibling plugin
pack.

### Declaration

`sources.toml` gains a `packs` table per source, beside `variants` and
`upstream`:

```toml
["michael-denyer/pstack-claude".packs]
pstack = "plugins/pstack"

["faviann/agent-skills".packs]
herdr = "plugins/herdr"
```

The key is the pack name. It follows the skill-name rules and is the link name.
The value is a source-relative tracked directory, validated like a variant tree:
normalized, tracked, no symlink traversal. The catalog validates each declared
pack when it loads, so install, `--check`, the selector and the update command
report the same errors:

- `.claude-plugin/plugin.json` is tracked there and its `name` equals the key.
- The directory holds `hooks/hooks.json` or `agents/`.
- The key is not a skill name in the same source, so `owner/repo:name` stays
  unambiguous.
- The directory has no untracked or ignored content, the rule a selected skill
  already follows.

### Selection

A profile line `michael-denyer/pstack-claude:pstack` resolves against the
source's skills first and its packs second. The profile format, the selector's
save format, `parse_selection` and `profile_problems` keep working. The selector
lists a pack beside the skills with its surface counts, for example
`pstack  pack  67 skills  4 agents  1 hook`.

A profile must not select a skill that sits inside a selected plugin pack.
Claude Code would load the same bytes twice, as `tdd` and `pstack:tdd`.
`profile_problems` reports it at the profile line. When the pack is not
selected, its skills stay selectable one by one. That is the fallback for a home
that blocks plugins (scenario 4).

### Materialization per harness

| Surface | Claude Code (`~/.claude/skills`) | Codex, Pi, OpenCode (`~/.agents/skills`) |
|---|---|---|
| Skill | skill link, unchanged | skill link, unchanged |
| Plugin pack | one link; skills load as `<pack>:<skill>`, agents as `<pack>:<agent>`; hooks run with `CLAUDE_PLUGIN_ROOT` set to the link path (observed: agent names and hooks in S1, skill names and the hook root in the design prototypes) | nothing (ADR 0004) |
| Instruction file | out of scope (ADR 0004) | out of scope |

Hook commands in a plugin pack refer to their scripts through
`${CLAUDE_PLUGIN_ROOT}`, never through a `sources/...` path. That keeps a later
change of link target, such as a pack store, free of hook edits.

### Who writes which files on a home

| Path | Writer (pack owner) | Skillset |
|---|---|---|
| `~/.claude/skills/<skill>`, `~/.agents/skills/<skill>` | skillset | receipt-owned links, as today |
| `~/.claude/skills/<pack>` | skillset | a receipt-owned link: the same `LinkRecord` and receipt directory as a skill |
| `~/.agents/.skillset/{profile,lock}`, `*/.skillset/receipts/` | skillset | unchanged |
| `~/.claude/settings.json` | Claude Code (`enabledPlugins`, `extraKnownMarketplaces`), chezmoi's `modify_settings.json` (its own keys and the `track-session-prs` hook), herdr's installer (`herdr-agent-state`), hand edits (today's herdr-playbook hook) | never written; scenario 0 removes the hand edit once |
| `~/.claude/plugins/**` | Claude Code | never written; `scripts/check-packs.sh` reads `claude plugin list --json` |
| `~/.claude/hooks/*.sh` | dotfiles, herdr | never touched |
| `~/.claude/CLAUDE.md`, `~/.agents/AGENTS.md` | dotfiles | out of scope |

Install and removal stay idempotent and convergent because a pack link is
planned, published and retired by the code path a skill link already uses.

### Checking what the harness loaded

Two failures are silent in Claude Code. A same-named marketplace plugin wins
over the pack, which then lists as `enabled: false` (observed, P3). Managed
settings with `strictKnownMarketplaces` lacking `{"source": "skills-dir"}`, or
with `strictPluginOnlyCustomization`, skip skills-dir plugins with no error
(documented). Install and `--check` stay offline and harness-free, so a separate
read-only `scripts/check-packs.sh`, about 30 lines (estimate), reads
`claude plugin list --json` and reports each plugin pack this home selects as
`enabled <version>`, `shadowed by <id>`, or `blocked or disabled`. It exits
non-zero on anything but `enabled`. The dotfiles hook may run it after install.

### The review gate

`update_sources.summarize` gains a list of the files a bump changes under
selected skills and selected packs, classified with `inventory.py`'s
categories. The summary prints them under **Hooks, agents and manifests**, and
`d` includes the pack directories in the diff. When the list contains a hook
configuration, hook script, agent, MCP file, or a plugin manifest newly added
inside a selected skill, `a` asks a second question before pushing: "This
update changes hooks or agents that run in every Claude Code session. Accept
them too? [y/N]". `N` skips the source. A changed plugin manifest or command
prompt is listed without the question, because pstack bumps its manifest
version on every release (8 of 8 bumps) and a question asked every time stops
being read. The selector's Ctrl+U overlay shows the same section and question.

The gate covers pin bumps, not the first selection of a pack. There the
selector's surface counts and the human reading `hooks/hooks.json` are the
gate. Claude Code has no per-hook approval (documented) and ran an edited hook
script in the next session without asking (observed in the design prototypes).

## Scenarios

Build cost counts new or changed lines (estimate, sized against comparable
existing code). Maintenance cost counts the human steps each occurrence needs,
what can drift, and how drift shows.

### 0. Prerequisite: herdr-playbook's hook becomes a pack

Today the hook script sits inside the skill,
`skills/herdr-playbook/hooks/session-start.sh`, and is registered by hand in
`~/.claude/settings.json`.

1. In `faviann/agent-skills`, add `plugins/herdr/.claude-plugin/plugin.json`
   (`{"name": "herdr"}`) and `plugins/herdr/hooks/hooks.json` with one
   `SessionStart` entry, matcher `startup|resume|clear|compact`, command
   `"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"`, timeout 5. Move the script
   there unchanged. It still prints nothing without `HERDR_ENV=1` (observed,
   S2).
2. Bump the pin through `scripts/update-sources.sh`. The new review section
   lists the files and asks the second question.
3. In skillset, declare `herdr = "plugins/herdr"` and add
   `faviann/agent-skills:herdr` to the workstation profile. Install links
   `~/.claude/skills/herdr`. The skill link `herdr-playbook` is unchanged and
   keeps its bare name in Claude Code and Codex (observed, S2 and C1).
4. Delete the hand-written `SessionStart` entry whose command starts
   `f="$HOME/.claude/skills/herdr-playbook/hooks/session-start.sh"`. Until then
   the primer is injected twice (observed, S2's seeded twin). Neither chezmoi
   nor herdr manages that entry.

Build cost: three files in agent-skills, about 15 lines; the pack feature from
scenario 1. Maintenance: none recurring. Five of 13 agent-skills bumps changed
this script, and each now gets the second question.

### 1. Add a source that ships hooks

Example: `addyosmani/agent-skills`, already pinned. Its root holds
`.claude-plugin/plugin.json`, 4 agents, 9 commands and 9 files under `hooks/`,
but no `hooks/hooks.json`. Its hook scripts are meant to be registered by hand
in a project's settings (its `hooks/SDD-CACHE.md`), so as a pack it brings 25
skills and 4 agents and runs no hook. Getting the hooks needs a fork that
declares them, through the existing fork mechanism. A brand-new source first
needs today's `git submodule add` step from
[Change sources by hand](../workflow.md#change-sources-by-hand).

1. Add `["addyosmani/agent-skills".packs] agent-skills = "."` to
   `sources.toml` and commit.
2. In `select-skills`, read the pack's `agents/` and `hooks/` in the checkout,
   select `agent-skills`, then Save and install.
3. Install links `~/.claude/skills/agent-skills` to the source root with its
   receipt. Nothing goes to `~/.agents/skills`.
4. Run `scripts/check-packs.sh`. The pack reports `enabled 0.6.15`.

Build cost (this scenario carries the feature): catalog parse, validation and
resolution of `packs`, about 80 lines; the double-load rule in
`profile_problems`, about 10; the selector row and counts, about 60;
`check-packs.sh`, about 30; tests, about 140. The reconciler needs no change,
because a pack is a `LinkRecord`. Maintenance: one `sources.toml` line and one
profile line per pack. Drift: shadowing or a managed block, reported by
`check-packs.sh`.

### 2. A pin bump that changes a hook script

Real cases: pstack `5851c896..7c045e56` changed `plugins/pstack/hooks/hooks.json`,
`hooks/codex-hooks.json`, `hooks/session-start-copilot.md` and the manifest
version (4 of the bump's 40 changed files are under `hooks/` and `.claude-plugin/`). In agent-skills, 5 of 13 bumps changed the
herdr script.

1. Run `scripts/update-sources.sh`, or press Ctrl+U on the source.
2. The summary lists the changed files under **Hooks, agents and manifests**.
   `d` shows the diff, including the pack directory.
3. `a` asks the second question. `y` pushes.
4. The live checkout fast-forwards and installs. No link changes; the link's
   target now holds the new content.
5. The next Claude Code session runs the new hook. No `claude plugin update`,
   no `settings.json` change.

Build cost: the classifier moved from `inventory.py` into the catalog, about 25
lines; the summary section, about 30; the second question in the command and the
overlay, about 40; tests with a fixture source whose second commit edits
`hooks/hooks.json` and third edits only a `SKILL.md`, about 80. Maintenance:
one extra `y` on bumps that touch a hook or agent (3 of 8 pstack bumps touched a
hook script and 2 its hook configuration; 5 of 13 agent-skills bumps). Drift:
none, because the link is live.

### 3. Remove a source

1. In a separate clone, remove the source's lines from every profile, its
   `sources.toml` entry (`variants`, `upstream`, `packs`) and the submodule.
   Commit and push.
2. In the live checkout, `git pull`, `./setup.sh`, then install.
3. Install retires the owned skill links and the pack link through their
   receipts.

Leftovers: `~/.claude/plugins/data/<pack>-skills-dir/` if the plugin wrote data,
and `enabledPlugins."<pack>@skills-dir": false` in `settings.json` if someone
ran `claude plugin disable`. Claude Code owns both; delete them by hand when
wanted.

Build cost: none. Maintenance: today's steps. Drift: a skipped install shows as
a stale owned link in `--check`.

### 4. A work machine without herdr

Facts: Linux, reaches github.com. Whether it sets Claude Code managed settings
is unknown.

1. In the selector, copy `workstation` as `work` and deselect
   `faviann/agent-skills:herdr-playbook`, `faviann/agent-skills:herdr` and other
   workstation-only skills such as `quest-3s`. Save and push.
2. The dotfiles hook passes `--profile workstation` to every new home today.
   Add a `work` machine profile to `.chezmoi.toml.tmpl` and pass the matching
   skillset profile, about 6 lines in dotfiles. Or run
   `scripts/reconcile-skills.sh --profile work` once by hand; the record sticks.
3. Run `scripts/check-packs.sh` on that machine. `blocked or disabled` for
   `pstack` means the managed settings block skills-dir plugins.
4. If blocked, choose one: deselect the `pstack` pack in `work` and select the
   pstack skills it needs, which keep their names and lose pstack's agents and
   hook; or, if the policy allows a directory marketplace, install pstack from a
   directory marketplace of that machine's live checkout. That route loads in
   place and follows the pin (observed by the arena judge), and its pack owner
   is the native marketplace.

herdr's own hooks are herdr's and dotfiles' to install or not. They exit
silently without `HERDR_ENV=1` (observed, S2).

Build cost: no skillset code; about 6 optional dotfiles lines. Maintenance: two
independent profiles, edited separately. Drift: a policy change on that
machine, seen only when `check-packs.sh` runs there.

### 5. Move pstack from a native plugin to skillset-managed

Today `pstack@pstack-claude` comes from the GitHub marketplace at `5851c896`
(0.9.114). The skillset pin is `7c045e56` (0.9.119). On 2026-11-01 both were at
`5851c896`; the next day's pin bump did not reach the native plugin, because
the two update paths are separate.

1. Declare `pstack = "plugins/pstack"`, select
   `michael-denyer/pstack-claude:pstack`, Save and install. Read the hook and
   agent diff between what runs today and the pin first:
   `git -C sources/michael-denyer/pstack-claude diff 5851c896 7c045e56 -- plugins/pstack/hooks plugins/pstack/agents`.
2. `scripts/check-packs.sh` reports `shadowed by pstack@pstack-claude`
   (observed, P3).
3. `claude plugin uninstall pstack@pstack-claude`, then
   `claude plugin marketplace remove pstack-claude`. The pack reports
   `enabled 0.9.119` and `extraKnownMarketplaces` is empty (observed, P3).
4. Delete `~/.claude/plugins/cache/pstack-claude/` by hand. It matters:
   pstack's `poteto-agent`, when its parent passes no path, searches
   `${CLAUDE_CODE_PLUGIN_CACHE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins}/`
   for `poteto-mode/SKILL.md` and uses the copy with the highest manifest version.
   With the cache left behind it silently reads the old 0.9.114 copy; with the
   cache gone it finds nothing and stops. Report that fallback upstream.
5. Update the README paragraph that says selecting pstack's skills would
   duplicate the native plugin.

`~/.claude/pstack-models.md` sits at the config root and carries over.

Build cost: none beyond scenario 1. Maintenance: once per home with a native
install. Drift: anyone reinstalling the marketplace plugin shadows the pack
again, and `check-packs.sh` reports it.

### Cost table

| Scenario | Code | Tests | Human steps per occurrence | Drift and detection |
|---|---|---|---|---|
| 0. herdr pack | 3 files in agent-skills (~15 lines) | 0 | once: delete one hand-written hook entry | double primer until deleted (S2 seed) |
| 1. Add a hook source | ~180 | ~140 | 1 `sources.toml` line, 1 profile line, read `hooks/` and `agents/` | shadowing, managed block: `check-packs.sh` |
| 2. Hook-changing bump | ~95 | ~80 | one extra `y` on hook or agent bumps | none; link is live |
| 3. Remove a source | 0 | 0 | today's steps | stale link in `--check` |
| 4. Work machine | 0 (~6 in dotfiles) | 0 | one profile, one `--profile work`, one `check-packs.sh` | policy change: `check-packs.sh` there |
| 5. pstack migration | 0 | 0 | 2 `claude plugin` commands, 1 cache delete, per home | re-shadowing: `check-packs.sh` |
| Total | ~275 | ~220 | | |

## ADR drafts

These go to `docs/adr/` when accepted. Status stays `proposed` until then.

### 0001-packs-are-skills-dir-plugin-links.md

```md
---
status: proposed
---
# Packs install as skills-dir plugin links

Skillset installs a pack that carries hooks or agents by linking its plugin
directory, inside the pinned live checkout, to `~/.claude/skills/<pack>`.
Claude Code loads it in place as `<pack>@skills-dir` with its skills, agents and
hooks. Skillset writes no harness configuration and needs no `claude` command at
install time. The link is the same receipt-owned object a skill link is, so
install, removal and interruption recovery reuse the existing code.

## Considered Options

- Writing hook entries into `~/.claude/settings.json` (issues 79 and 109). That
  file already has four writers: Claude Code, chezmoi, herdr and hand edits.
- A skillset directory marketplace (`.claude-plugin/marketplace.json` with
  relative `./sources/...` entries). It loads in place and follows the pin, and a
  managed block fails loudly at `marketplace add`. Rejected: it needs the
  `claude` CLI during install, writes `enabledPlugins` and a machine-absolute
  `extraKnownMarketplaces` path into `settings.json`, renames pstack to
  `pstack@skillset`, and `claude plugin validate` passes with a dangling entry.
- A GitHub marketplace per source. Its version moves separately from the pin;
  native pstack lagged the pin by one bump.
- One generated plugin per profile. Every skill would be renamed
  `<profile>:<skill>`.

## Consequences

A same-named marketplace plugin shadows the pack, and managed settings can skip
skills-dir plugins silently. `scripts/check-packs.sh` reports both. Supersedes
issues 79 and 109.
```

### 0002-plugin-packs-are-declared.md

```md
---
status: proposed
---
# Plugin packs are declared in sources.toml

A plugin pack is declared as `["owner/repo".packs] name = "path"` and selected
with the same `owner/repo:name` profile line as a skill. It must carry hooks or
agents. Packs are not discovered from manifests: eight of nine sources ship
manifests, mostly for skills-only bundles that profiles already choose skill by
skill. A skill directory may not carry its own manifest, because Codex then
renames the skill `<name>:<name>`; a skill's hooks go in a sibling plugin pack.
A profile may not select a skill that sits inside a selected plugin pack.

## Considered Options

- A `pack:` prefix in profiles, or a second profile file. The catalog can
  resolve a name against skills and then packs, so no profile tool changes.
- Treating any manifest-carrying skill directory as a pack. It keeps the bare
  name in Claude Code but not in Codex.
```

### 0003-hook-changes-are-gated-at-the-pin-bump.md

```md
---
status: proposed
---
# Hook and agent changes are gated at the pin bump

The per-source update review lists the files a bump changes under selected
skills and packs, classified as hook, agent, manifest, command or MCP files.
A hook, agent or MCP change, or a manifest newly added inside a selected skill,
needs a second accept. Manifest and command changes alone are listed without
the question, because pstack changes its manifest on every release.

There is no content hash and no harness-side trust. Claude Code has no per-hook
approval. Codex's `trusted_hash` covers the declared command, not the script
bytes, so this gate does not protect a Codex hook. Issue 112 records that gap
and the content-addressed alternative for when Codex hooks come into scope.
```

### 0004-packs-carry-skills-hooks-and-agents.md

```md
---
status: proposed
---
# Packs carry skills, hooks and agents; hooks and agents reach Claude Code only

Plugin packs link into `~/.claude/skills` only. Codex, OpenCode and Pi keep plain
skill links in `~/.agents/skills`. Codex plugins copy into a version-keyed
cache and need a trust entry per hook over the whole plugin tree; OpenCode and
Pi hooks are code modules with no declarative form; no pinned source needs a
hook outside Claude Code.

Instruction files stay with dotfiles and are not a pack surface. The one known
consumer, `mattpocock/skills:wayfinder` losing glossary edits between sessions,
is fixed at the skill instead.

Supersedes issue 80 and the README's "harness packages". Issue 112 tracks the
other harnesses.
```

## Glossary changes

In `CONTEXT.md`, add after **Skill**:

```md
**Pack**:
A set of surfaces from one source at its pin that a profile selects as one unit.
A pack of skills installs as skill links; a plugin pack installs as one link.
_Avoid_: Execution pack, bundle, harness package, binding

**Plugin pack**:
A pack that is a Claude Code plugin directory carrying hooks or agents, declared
in `sources.toml` and installed as one link in `~/.claude/skills`, which Claude
Code loads as `<pack>@skills-dir`.
_Avoid_: Skills-dir plugin (when meaning the pack), generated plugin

**Surface**:
One kind of thing a pack carries: a skill, a hook or an agent. Instruction
files are reserved and no pack carries one.
_Avoid_: Component, asset, binding

**Pack owner**:
Whoever writes a pack's files on a home: skillset for linked packs, a native
marketplace for plugins Claude Code installs, herdr for its own hooks, dotfiles
for what chezmoi renders.
_Avoid_: Installer, provider

**Shadowed**:
Said of a plugin pack that Claude Code does not load because a same-named plugin
from a marketplace wins or managed settings block skills-dir plugins.
_Avoid_: Masked, overridden, conflicting
```

Edit these entries:

- **Skill**: change the avoid line to `_Avoid_: Plugin, package (a plugin pack is installed as a plugin; a skill is not)`.
- **Catalog**: "The skills and declared plugin packs the selector offers for selection."
- **Selection**: "The set of skills and packs chosen to be installed."
- **Profile**: "A named selection of skills and packs, kept as its own file."
- **Install directory**: add "Plugin pack links go into `~/.claude/skills` only."
- **Install**: "Make every install directory of a home match its active profile's committed selection, adding and removing skill and pack links."

Retire nothing in `CONTEXT.md`. "Binding" (issue 80) and "harness package" (the
README) appear only in Pack's avoid line.

## Validation plan

[`tests/harness-premises.sh`](../../tests/harness-premises.sh) checks every harness behavior this design
rests on, against the installed `claude` and `codex`, in throwaway homes under
`mktemp -d` that it deletes on exit. It never touches `~/.claude` or `~/.codex`.
Most checks have a seeded twin, a deliberately broken setup the check must
reject, so a check that always passes shows up.

```bash
tests/harness-premises.sh                       # no model calls, about 10 s
tests/harness-premises.sh --sessions --codex    # adds 4 haiku sessions and 2 Codex runs
```

Pass: the last line is `0 failed` and the exit code is 0. Last run: 2026-11-03,
Claude Code 2.1.316, Codex 0.166.0, `0 failed` (22 checks: 14 without flags, 6 with `--sessions`, 2 with `--codex`).

| Check | Pass condition | Seeded twin |
|---|---|---|
| P1 | `pstack` linked into `skills/` lists as `pstack@skills-dir`, enabled, at the manifest's version; no `settings.json` is written | a link to a directory without a manifest lists as absent |
| P2 | `claude plugin details` shows the pinned tree's skill, agent and hook counts | a copy without `agents/` is caught |
| P3 | after `plugin uninstall` and `marketplace remove`, the pack is enabled again and `extraKnownMarketplaces` is empty | with the marketplace plugin installed, the pack lists `false unknown` |
| P4 | a hook-only `herdr` pack lists with one hook and no skills; the plain `herdr-playbook` skill directory is not a plugin | |
| P5 | the managed-settings parser lets an allowlist naming `skills-dir` through | `strictKnownMarketplaces` without `skills-dir`, and `strictPluginOnlyCustomization`, are flagged |
| S1 | in a headless session, pstack's hook output and `pstack:poteto-agent` are in the transcript | |
| S2 | with `HERDR_ENV=1`, the herdr primer appears exactly once and `herdr-playbook` keeps its bare name; without it, no primer | today's hand-written `settings.json` entry beside the pack doubles the primer |
| C1 | Codex lists the plain skill as `herdr-playbook` | a manifest inside the skill directory renames it `herdr-playbook:herdr-playbook` |

Session checks read the transcript's `hook_success`, `skill_listing` and
`agent_listing_delta` entries, not the model's reply. They clear `HERDR_*`
variables first, so a run from inside herdr does not leak them into the
no-herdr case.

Checks that need the build, written with it:

- **V6 removal converges.** In `tests/test_reconcile.py`: a profile selecting a
  plugin pack installs its link and receipt; switching to a profile without it
  removes both; `--check` then reports reconciled. Seeded twin: a foreign
  symlink at the pack's name is refused as an unowned collision.
- **V7 the review gate.** In `tests/test_update_sources.py`, with
  `tests/fixture.py`'s real Git sources: a bump editing a pack's
  `hooks/hooks.json` shows the section and asks twice; a bump editing only a
  `SKILL.md` asks once. Seeded twin: narrowing the classifier to `hooks/*.sh`
  fails the first case.
- **V8 the double-load rule.** A profile selecting `pstack` and a skill inside
  `plugins/pstack` fails `--check` at that line.
- **V9 the work machine.** On that machine, against its real home:
  `scripts/check-packs.sh` reports `enabled` for every selected plugin pack, and
  `jq '{strictKnownMarketplaces, strictPluginOnlyCustomization}' /etc/claude-code/managed-settings.json`
  matches P5's parser. Seeding the harness itself needs root and a container.

## Rejected and deferred

- **A pack store.** Install would export each pack at its pin into a read-only,
  content-addressed directory and link there instead of into the live checkout.
  Its arena prototype passed its checks, at about 140 lines and 250 test lines on
  top of this design. It protects against a risk this home has not hit:
  something other than `update-sources` changing `sources/` between installs.
  Revisit when one of these happens: Codex hooks come into scope (the tree id in
  a hook command makes Codex's declaration hash cover the script); the Zeroshot
  image build starts (issues 88 and 92 need read-only copies with provenance);
  the live checkout must move; or a session is caught running source bytes no
  install accepted. Until then the first store install would move every link
  through the existing relocation path, with no migration code.
- **Hook content hashes and skillset-written Codex trust.** They protect nothing
  on Claude Code, and Codex hooks are out of scope.
- **Linking plugin packs into `~/.agents/skills`.** Codex would rename their
  skills and run no hook.
- **A harness view inside `--check`.** It would make CI and offline checks
  depend on a `claude` binary. `check-packs.sh` sits beside it instead.
- **A gate on first selection.** Two packs exist and the owner runs both.
  If a third-party pack with hooks is ever selected casually, show its
  `hooks/hooks.json` in the save dialog, about 30 lines.

## Open questions

1. Managed settings on the work machine. Unknown until V9 runs there.
2. Whether `"."` is an acceptable pack path. It links the whole source checkout,
   `.git` file included, as the plugin. Claude Code reads only the component
   directories (documented). The alternative is refusing `"."`.
3. Whether OpenCode, which also reads `~/.claude/skills`, descends into
   `pstack/skills/*`. If it does, pstack's skills reach OpenCode without their
   agents.
4. The plugin data directory changes from `pstack-pstack-claude` to
   `pstack-skills-dir` on migration. It is empty today.
5. Zeroshot targets see no user-level plugins or hooks (issue 88). A pack written
   into a target arrives as skills only. Deferred with issues 92 and 93.

## Follow-ups

- `faviann/agent-skills`: add the `herdr` plugin pack (scenario 0).
- Upstream pstack: its `poteto-agent` fallback searches only the plugin cache.
- The wayfinder glossary case (issue 80): a fork patch in `faviann/skills-mattpocock`
  or a report upstream with a real run.
- Other harnesses and the Codex trust gap: [issue 112](https://github.com/faviann/skillset/issues/112).
