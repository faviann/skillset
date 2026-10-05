# Harness frontmatter rules for linked skills

Research for [#19](https://github.com/faviann/skillset/issues/19), a child of
the map [#13](https://github.com/faviann/skillset/issues/13). Written
2026-10-05.

## Answer

Every canonical skill in the current sources loads unchanged in every harness
that reads `~/.claude/skills` or `~/.agents/skills`. No skill needs a
harness-specific variant to load. The pinned sources were scanned with each
harness's own loader: 203 selectable skills, plus the b1rdmania repo-root skill.

- **Unknown keys are ignored by every harness.** Claude Code, Codex, Pi and
  OpenCode all ignore them silently. None rejects or warns on them.
- **No current skill hits any cap.** The longest description is 976
  characters (rampstack `logo-design`), the longest name is 50 characters, and
  the largest `SKILL.md` is 36 KB.
- **The remaining differences change behaviour, not loading.** The one that
  users see is in Codex: it prefixes the plugin name when a linked skill lives
  inside a plugin repository, so `tdd` from pstack is listed as `pstack:tdd`.
- **`~/.agents/skills` is read by Codex, Pi and OpenCode.** Claude Code does
  not load it. OpenCode reads both directories, so it sees every skillset skill
  twice.

## Versions examined

| Harness | Installed here | Source read | Commit |
| --- | --- | --- | --- |
| Claude Code | 2.1.289 | [Skills docs](https://code.claude.com/docs/en/skills), fetched 2026-10-04, and strings from the shipped binary. The source is not public. | none |
| Codex CLI | 0.160.0 | [`openai/codex`](https://github.com/openai/codex) tag `rust-v0.160.0` | `a956835d020762cb2b570053af06f643a11c0ecc` |
| Pi | 0.87.1 (`@earendil-works/pi-coding-agent`) | [`earendil-works/pi`](https://github.com/earendil-works/pi) tag `v0.87.1` (`badlogic/pi-mono` now redirects here) | `f07218c4d4bbc12bef056a7058c3dd49dfe41abe` |
| OpenCode | 1.18.32 (`opencode-ai`) | [`anomalyco/opencode`](https://github.com/anomalyco/opencode) tag `v1.18.32` (formerly `sst/opencode`) | `545f51d26cc39a907d2867492d498d9607ea5fa4` |

OpenCode is outside the ticket's three harnesses. It is installed on this
workstation and reads both skill directories, so it is covered too.

Permalinks below use these commits. "Probe" means a planted test skill run
through the real loader (see [Method](#method)).

## Which harness reads which directory

| Harness | `~/.claude/skills` | `~/.agents/skills` | Other roots |
| --- | --- | --- | --- |
| Claude Code | yes | **no** | Project `.claude/skills` up to the repo root, managed settings, plugins, `--add-dir`. |
| Codex | no | **yes**, user scope | `$CODEX_HOME/skills` (deprecated), `/etc/codex/skills`, and `.agents/skills` in each directory from the project root down to the working directory. |
| Pi | no | **yes**, user scope, loaded without project trust | `~/.pi/agent/skills`, `.pi/skills`, and `.agents/skills` in each directory from the working directory up to the git root (these need project trust). |
| OpenCode | yes | **yes** | `~/.config/opencode/skills`, project `.opencode`, `.claude/skills` and `.agents/skills` up to the worktree. |

Evidence:

- **Claude Code.** The docs list personal skills only at
  `~/.claude/skills/<skill-name>/SKILL.md`. In the 2.1.289 binary,
  `~/.agents/skills` appears only beside Cursor paths (`~/.cursor/skills`), in
  what looks like a config importer that writes into `~/.claude/skills`. That
  reading of minified code is an inference.
- **Codex.** Code:
  [`host_roots.rs` L95-L120](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/host_roots.rs#L95-L120)
  and
  [L137-L185](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/host_roots.rs#L137-L185).
  The [Codex skills docs](https://developers.openai.com/codex/skills) list
  `$HOME/.agents/skills` as the `USER` location and say symlinked skill folders
  are followed.
- **Pi.** Code:
  [`package-manager.ts` L2397-L2400](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/package-manager.ts#L2397-L2400)
  and
  [L2488-L2500](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/package-manager.ts#L2488-L2500).
  The [Pi skills docs](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/docs/skills.md)
  say: "Pi also supports the Agent Skills locations `~/.agents/skills/` and
  `.agents/skills/`."
- **OpenCode.** Code:
  [`skill/index.ts` L185-L201](https://github.com/anomalyco/opencode/blob/545f51d26cc39a907d2867492d498d9607ea5fa4/packages/opencode/src/skill/index.ts#L185-L201)
  and the
  [OpenCode skills docs](https://github.com/anomalyco/opencode/blob/545f51d26cc39a907d2867492d498d9607ea5fa4/packages/web/src/content/docs/skills.mdx).

Not checked: Gemini CLI, Cursor, Amp and other harnesses. None of them is
installed here.

## Rules per harness

### Claude Code 2.1.289

Source: the [frontmatter reference](https://code.claude.com/docs/en/skills#frontmatter-reference).
The shipped binary corroborates the points marked "binary".

- **Recognised keys:** `name`, `description`, `when_to_use`, `argument-hint`,
  `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`,
  `disallowed-tools`, `model`, `effort`, `context`, `agent`, `background`,
  `hooks`, `paths`, `shell`, `metadata`, `license`, `compatibility`.
- **Unknown keys:** ignored. The docs say: "Claude Code ignores a field it
  doesn't recognize without reporting an error."
- **Invalid YAML:** the skill still loads with no fields set. The binary logs
  `[skills] YAML frontmatter in … failed to parse and was ignored`. The probe
  with an unquoted `: ` in the description loaded.
- **`name`:** optional. A personal skill's identity is its directory name. No
  length or character check was seen: the probe `name: Bad_Name` loaded as
  `bad-name`, its directory name.
- **`description`:** optional. Without it, Claude Code uses the first
  non-empty body line. The skill listing truncates `description` plus
  `when_to_use` at 1,536 characters (setting `skillListingMaxDescChars`). Length
  never drops a skill.
- **Booleans:** `yes`, `no`, `on`, `off`, `1` and `0` are accepted since
  v2.1.218.
- **`metadata`:** kept only when it is a map (docs, and binary
  `metadata:N(e.metadata)?e.metadata:void 0`).
- **What drops a skill:**
  - A `SKILL.md` over 1,000,000 bytes (binary: `Ex=1e6`, "bytes exceeds …
    byte limit"; the 1.1 MB probe was dropped).
  - A folder named `synced` in any capitalization.
  - A folder or `name` equal to `anthropic-skills` or starting with
    `anthropic-skills:`. These names are reserved for claude.ai sync.
- **Listing budget:** 1% of the context window. When the listing is over
  budget, descriptions are dropped first for the least-used skills.
- **Stricter paths elsewhere:** claude.ai upload, the Skills API and
  `package_skill.py` allow only `name`, `description`, `license`,
  `compatibility`, `metadata` and `allowed-tools`. Any other key is a hard
  `Unexpected key(s)` error. That rule does not apply to `~/.claude/skills`.

### Codex CLI 0.160.0

- **Recognised frontmatter keys:** `name`, `description` and
  `metadata.short-description`. See
  [`parser.rs` L6-L20](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/parser.rs#L6-L20).
- **Unknown keys:** ignored silently. The serde struct has no
  `deny_unknown_fields`, and the test
  [`repairs_unrecognized_frontmatter_fields_that_need_quotes`](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/parser_tests.rs#L63-L79)
  loads a skill with `argument-hint` and `tags`. The 0.118.0 loader had no
  `deny_unknown_fields` either.
- **Invocation policy, UI text and tool dependencies:** these come from
  `agents/openai.yaml` beside `SKILL.md`, not from frontmatter.
  `policy.allow_implicit_invocation: false` is the counterpart of
  `disable-model-invocation`. See
  [`metadata.rs` L49-L55](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/metadata.rs#L49-L55)
  and the docs section "Optional metadata".
- **YAML:** parsed with `serde_yaml`. On failure, Codex retries once after
  quoting scalars that contain `: ` or start with `[`, `{`, `@` or a backtick.
  If the YAML is still invalid, the skill is dropped and reported as an error.
  See
  [`parser.rs` L48-L62 and L98-L181](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/parser.rs#L48-L181).
- **What drops a skill**
  ([`parser.rs` L44-L92 and L183-L221](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/parser.rs#L44-L221)):
  - No frontmatter block: the first line must be `---`, and a closing `---`
    must follow with something between them.
  - An empty or missing `description`.
  - A `name` over 64 characters, after collapsing whitespace. A missing name
    falls back to the directory name. There is no character check: the probe
    `Bad_Name` loaded as is.
  - A qualified name `<plugin>:<name>` over 129 characters. See
    [`host.rs` L321](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/host.rs#L321).
- **Description length:** since 0.142.0 it no longer drops a skill.
  - [PR #29006](https://github.com/openai/codex/commit/64bdeed9f7adbe60c725153b3fb74ed044a36221)
    (merged 2026-06-19, first released in `rust-v0.142.0`) moved the
    1,024-character cap to the rendered catalog, which shows 1,021 characters
    plus `...`.
  - Through 0.141.x, a description over 1,024 characters dropped the skill with
    `invalid description: exceeds maximum length of 1024 characters`. That is
    what rampstack saw on 0.118.0.
  - Checked: `core-skills/src/loader.rs` calls
    `validate_len(&description, MAX_DESCRIPTION_LEN, …)` at `rust-v0.141.0`
    line 683, and the call is gone at `rust-v0.142.0`. The 0.160.0 test
    [`preserves_overlong_descriptions_and_short_descriptions`](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/parser_tests.rs#L99-L118)
    pins the new behaviour.
- **Catalog budget:** 2% of the context window, or 8,000 characters when the
  window is unknown. `[skills] max_context_tokens` can raise it to at most
  10,000 tokens. Codex shortens descriptions first and may omit skills with a
  warning. See
  [`render.rs` L19-L25](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/render.rs#L19-L25)
  and the docs.
- **Discovery:**
  - Recursive, down to depth 6 below each root.
  - Follows directory symlinks for user, repo and admin roots.
  - Skips hidden directories below the root.
  - A nested `SKILL.md` inside a linked skill therefore loads as an extra
    skill. See
    [`discovery.rs` L54-L150](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/discovery.rs#L54-L150)
    and
    [`host.rs` L163-L166](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/host.rs#L163-L166).
- **Plugin namespace for symlinked skills:**
  - When a skill is reached through a symlink, Codex takes the resolved path's
    parent as a namespace root ([`host.rs` L263-L267](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/host.rs#L263-L267)).
  - It then walks up from there to the nearest `.codex-plugin/plugin.json`,
    `.claude-plugin/plugin.json` or `.cursor-plugin/plugin.json`
    ([`namespace.rs` L11-L24](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/ext/skills/src/loader/namespace.rs#L11-L24),
    [`exec-server-protocol` L49-L53](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/exec-server-protocol/src/protocol.rs#L49-L53)).
  - The skill's name becomes `<plugin name>:<name>`.
  - Plain `$name` mentions are matched against that qualified name
    ([`selection.rs` L171-L176](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/skills/src/selection.rs#L171-L176)).
    This is from reading the source; mention matching was not run.

### Pi 0.87.1

- **Recognised keys:** `name`, `description` and `disable-model-invocation`.
  The type ends in `[key: string]: unknown`, and nothing else is read. See
  [`skills.ts` L67-L72](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L67-L72).
  The docs list the Agent Skills fields (`license`, `compatibility`,
  `metadata`, `allowed-tools`) as portable, but the loader does nothing with
  them.
- **Unknown keys:** ignored, with no diagnostic.
- **YAML:** parsed with the strict `yaml` package
  ([`frontmatter.ts`](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/utils/frontmatter.ts)).
  A parse error yields a warning, and **the skill is not loaded**
  ([`skills.ts` L293-L302](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L293-L302)).
  The unquoted-colon probe was dropped.
- **What drops a skill:** invalid YAML, or a missing or blank `description`
  ([L304-L332](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L304-L332)).
- **Warnings only; the skill still loads**
  ([L92-L127](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L92-L127)):
  - A description over 1,024 characters.
  - A name over 64 characters, with characters outside `[a-z0-9-]`, with a
    leading or trailing hyphen, or with `--`.
  - Pi does not check that the name matches the directory.
- **`disable-model-invocation`:** honoured only when the value is the YAML
  boolean `true`
  ([L341](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L341)).
  The probe `disable-model-invocation: yes` parsed as a string, so the skill
  was not hidden. Claude Code treats `yes` as true. All current sources use
  `true`.
- **Name collisions:** the first skill with a name wins and later ones get a
  collision diagnostic. The same real file reached through two symlinks is
  skipped silently
  ([L421-L450](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/skills.ts#L421-L450)).
- **Discovery:** a directory containing `SKILL.md` is a skill root and Pi does
  not recurse below it. Dot entries and `node_modules` are skipped, and
  `.gitignore`, `.ignore` and `.fdignore` are honoured
  ([`package-manager.ts` L363-L442](https://github.com/earendil-works/pi/blob/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent/src/core/package-manager.ts#L363-L442)).

### OpenCode 1.18.32

- **Docs vs code:** the docs say only `name`, `description`, `license`,
  `compatibility` and `metadata` are recognised and that unknown fields are
  ignored. They also say `name` must match `^[a-z0-9]+(-[a-z0-9]+)*$`, be at
  most 64 characters and match the directory, and that `description` must be
  1 to 1,024 characters.
- **What the code checks:** only that `name` is a string and `description` is
  absent or a string
  ([`skill/index.ts` L53-L59, L105-L140](https://github.com/anomalyco/opencode/blob/545f51d26cc39a907d2867492d498d9607ea5fa4/packages/opencode/src/skill/index.ts#L53-L140)).
- **What drops a skill:**
  - A missing `name`. The skill is dropped silently, with no log line.
  - YAML that still fails after OpenCode's retry, which rewrites top-level
    values containing `:` as block scalars
    ([`core/config/markdown.ts`](https://github.com/anomalyco/opencode/blob/545f51d26cc39a907d2867492d498d9607ea5fa4/packages/core/src/config/markdown.ts)).
- **Not enforced:** length, character set and directory match. The probes
  `Bad_Name` and descriptions of 1,500 and 2,000 characters all loaded.
- **Duplicate names:** a later skill replaces an earlier one, with a
  `duplicate skill name` warning. OpenCode scans both `~/.claude/skills` and
  `~/.agents/skills`, so each skillset skill logs this warning once. Both
  entries resolve to the same target, so it is harmless.
- **No `disable-model-invocation`:** skill access is controlled through the
  permission config instead.

## Keys used by the current sources

Counts are over the 204 canonical `SKILL.md` files: all six sources, excluding
rampstack's `dist/` ports.

| Key | Where (count) | Claude Code | Codex | Pi | OpenCode |
| --- | --- | --- | --- | --- | --- |
| `name` | all (204) | display name; the directory name is the identity | name, prefixed with the plugin namespace when the link target is in a plugin | name | required |
| `description` | all (204) | listing text, truncated at 1,536 | catalog text, truncated at 1,024 | prompt text | tool listing |
| `disable-model-invocation` | mattpocock (22), humanlayer `show-me` (1) | honoured | ignored; the same intent comes from `agents/openai.yaml`, which all 23 ship with `allow_implicit_invocation: false` | honoured | ignored |
| `user-invocable` | pstack `principle-*` (23) | honoured; hidden from `/` | ignored | ignored | ignored |
| `argument-hint` | mattpocock (4) | honoured | ignored | ignored | ignored |
| `paths` | pstack `typescript-best-practices` (1) | honoured; listed only once matching files are touched | ignored; always listed | ignored | ignored |
| `metadata` | mattpocock `pr` (1, a nested map) | kept, not acted on | only `short-description` is read; absent here | ignored | ignored |
| `category`, `catalog_summary`, `display_order` | rampstack (103 each) | ignored | ignored | ignored | ignored |

"Ignored" never causes a warning or a drop in any of the four harnesses.

## Probe results

Planted skills, run through each harness's own loader.

| Case | Claude Code | Codex | Pi | OpenCode |
| --- | --- | --- | --- | --- |
| Unquoted colon: `description: Use when: things break: badly` | loads | loads (repaired) | **dropped**, with a warning | loads (repaired) |
| Unknown keys `category`, `catalog_summary`, `display_order` | loads | loads | loads | loads |
| 1,500-character description | loads | loads, full text kept | loads, with a warning | loads |
| 2,000-character description | not run | loads | loads, with a warning | loads |
| No `name` key | loads under its directory name | loads under its directory name | loads under its directory name | **dropped silently** |
| `name: Bad_Name` | loads as `bad-name` | loads as `Bad_Name` | loads, with a warning | loads as `Bad_Name` |
| `disable-model-invocation: yes` | true (docs, v2.1.218+) | ignored | false (string) | ignored |
| `SKILL.md` larger than 1 MB | **dropped** | loads | loads | loads |

## Scan of the current sources

All 204 canonical `SKILL.md` files parse as strict YAML. Each has a `name` and
a non-empty `description`. Every `name` matches the spec pattern and equals its
directory name. The one exception is the b1rdmania repo-root `SKILL.md`, whose
name is `plain-english` in directory `claude-plain-english-skill`. skillset
cannot select that skill anyway, because the directory contains nested skills.

| Source | Skills | Longest description (chars) | Codex name form |
| --- | --- | --- | --- |
| rampstackco/claude-skills | 103 | 976 (`logo-design`) | `rampstack-skills:<name>` |
| michael-denyer/pstack-claude | 54 | 421 | `pstack:<name>` |
| mattpocock/skills | 37 | 417 | `mattpocock-skills:<name>` |
| humanlayer/skills | 6 | 214 | `<plugin>:<name>`, for example `show-me:show-me` |
| b1rdmania/claude-plain-english-skill | 2 + root | 617 | `plain-english:<name>` |
| faviann/agent-skills | 1 | 222 | `publish-artifact` (no plugin manifest) |

Results per harness, for the 203 selectable skills:

- **Codex 0.160.0:** all 203 load, with zero errors.
- **Pi 0.87.1:** all 204 load, with zero diagnostics.
- **OpenCode 1.18.32:** all 203 load. The only messages are the 203 expected
  duplicate-name warnings.
- **Claude Code 2.1.289:** the session's `init` event lists 179 of the 203.
  The 24 missing are the 23 pstack `principle-*` skills, which set
  `user-invocable: false`, and `typescript-best-practices`, which sets `paths`.
  The init list appears to contain only user-invocable skills available now.
  That explanation is an inference from the docs; these skills are not
  dropped. All 203 are far below the 1 MB size cap.

The two rampstack ports are not needed for loading:

- `dist/pi/.agents/skills` is byte-identical to `skills/` (checked with
  `diff -r`).
- `dist/codex` only moves the three ignored catalog keys into a sidecar file.

## Verdict

**No skill in the current sources needs a harness-specific variant.** The
canonical directory can be linked into both `~/.claude/skills` and
`~/.agents/skills` as it is.

The remaining differences change behaviour, not whether a skill loads:

1. **Codex renames symlinked plugin skills.** Every source except
   faviann/agent-skills gets a `<plugin>:` prefix in Codex, so the explicit
   invocation is `$pstack:tdd`, not `$tdd`. A rampstack-style variant does not
   change this, because `dist/codex` sits under the same `.claude-plugin`.
   Copying the files instead of linking them would avoid the prefix. That is an
   inference from the code: the prefix comes from the resolved path.
2. **Manual-only skills:** Claude Code and Pi honour
   `disable-model-invocation`. Codex gets the same effect from the
   `agents/openai.yaml` that every affected skill already ships. OpenCode has no
   frontmatter equivalent.
3. **Claude-only keys:** `user-invocable: false`, `paths` and `argument-hint`
   take effect only in Claude Code. Elsewhere those skills are listed and
   invocable normally.

## rampstack's port notes, checked

Notes: `sources/rampstackco/claude-skills/dist/{codex,pi}/PORT_NOTES.md`.

| Claim | Status |
| --- | --- |
| Codex drops a skill whose description is over 1,024 characters (codex-cli 0.118.0). | True through 0.141.x. **No longer true** from 0.142.0; the cap now applies only to the catalog. |
| Codex (and Antigravity) check frontmatter against a fixed set of keys and "reject or warn" on unknown keys. | **Not true for Codex**, in the 0.118.0 or 0.160.0 source. Codex 0.160.0 loads all 103 canonical rampstack skills, unknown keys included, with zero errors. Antigravity was not checked. The hard unknown-key error does exist for claude.ai upload and the Skills API (Claude Code docs). |
| Codex reads `$HOME/.agents/skills`. | True. |
| Pi's frontmatter type is `{ name?, description?, "disable-model-invocation"?, [key]: unknown }`, and arbitrary keys are ignored. | True. |
| In Pi, an over-length description is only a warning, and only a missing description prevents loading. | Partly true. **Invalid YAML also prevents loading.** |
| Pi name rules: lowercase `a-z`, `0-9` and hyphen, at most 64 characters, no consecutive hyphens. | True, but violations are warnings only. |

## Implications for skillset

These are inferences, not decisions.

- **Cheap reconciler checks that catch real drops:**
  - A strict YAML parse (Pi drops on failure).
  - A non-empty `description` (Codex and Pi drop).
  - A `name` key (OpenCode drops). The reconciler's existing name-matches-directory rule already covers this.
  - A name of at most 64 characters (Codex drops).
  - A `SKILL.md` under 1 MB (Claude Code drops).
  - The existing ban on nested `SKILL.md` files, which matters because Codex
    scans recursively.
  - Optionally, a warning when a description exceeds 1,024 characters (Codex
    before 0.142 drops; Pi warns).
- **The selector should not assume one name across harnesses.** Codex shows
  `<plugin>:<name>` for linked plugin skills.
- **Selecting many skills shrinks descriptions.** The listing budgets are 1% of
  the context window in Claude Code and 2% (or 8,000 characters) in Codex.
  rampstack measured Codex cutting each description to about 535 characters
  with 108 skills installed. That is a reason to keep selections small; it is
  not a load failure.

## Method

- **Survey and Pi:** for each canonical `SKILL.md`, a Node script parsed the
  frontmatter with Pi's bundled `yaml` package. It then called
  `loadSkillsFromDir` from the installed `@earendil-works/pi-coding-agent`
  0.87.1 on the skill directory.
- **Isolated homes:** for each source, a temporary `HOME` held
  `.agents/skills/<dir>` and `.claude/skills/<dir>` symlinks to the canonical
  directories, mirroring skillset's layout.
- **Codex:** `codex app-server` (0.160.0) ran with that `HOME` and a fresh
  `CODEX_HOME`. A JSON-RPC `skills/list` request with `forceReload: true`
  returned the loaded skills and per-file errors.
- **OpenCode:** `opencode debug skill` (1.18.32) ran with that `HOME` and
  isolated XDG directories, logging warnings to stderr.
- **Claude Code:** `claude -p` (2.1.289) ran in a project whose `.claude/skills`
  held the symlinks, with `--setting-sources project,local` so personal skills
  could not mask results. The script read the `skills` list from the
  `system/init` stream event, then stopped the process.
- **`claude plugin validate` gave no evidence.** On 2.1.289 it reported zero
  components for every skills directory tried, including planted invalid
  skills, so it was not used.
- **Probe skills** were planted the same way for each case in
  [Probe results](#probe-results).

## Sources

- Claude Code docs, "Extend Claude with skills": <https://code.claude.com/docs/en/skills> (markdown at `…/skills.md`), fetched 2026-10-04.
- Codex docs, "Build skills": <https://developers.openai.com/codex/skills> (redirects to `learn.chatgpt.com/docs/build-skills`), fetched 2026-10-04.
- Codex source, tag `rust-v0.160.0`: <https://github.com/openai/codex/tree/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs>. Older loaders were read through the GitHub API at tags `rust-v0.118.0`, `rust-v0.141.0` and `rust-v0.142.0` (`codex-rs/core-skills/src/loader.rs`).
- Codex PR #29006, "Preserve skill descriptions outside model context": <https://github.com/openai/codex/commit/64bdeed9f7adbe60c725153b3fb74ed044a36221>.
- Pi source and docs, tag `v0.87.1`: <https://github.com/earendil-works/pi/tree/f07218c4d4bbc12bef056a7058c3dd49dfe41abe/packages/coding-agent>.
- OpenCode source and docs, tag `v1.18.32`: <https://github.com/anomalyco/opencode/tree/545f51d26cc39a907d2867492d498d9607ea5fa4>.
- Agent Skills specification: <https://agentskills.io/specification>. This defines the portable format, not any harness's behaviour.
- rampstack port notes, a claim to verify, not a primary source: `sources/rampstackco/claude-skills/dist/codex/PORT_NOTES.md` and `dist/pi/PORT_NOTES.md` at the pinned commit `3d4510a`.
