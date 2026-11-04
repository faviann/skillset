#!/usr/bin/env bash
# Checks the harness behavior the pack design rests on, against the installed Claude Code
# (and optionally Codex), in throwaway homes. Never touches the real ~/.claude or ~/.codex.
#
# Usage: tests/harness-premises.sh [--sessions] [--codex]
#   --sessions  also run headless Claude Code sessions (haiku; needs ~/.claude credentials and env)
#   --codex     also run two Codex sessions (needs ~/.codex/auth.json)
# Each check prints "ok" or "FAIL". Seeded twins are checks built to fail, so a check that always passes shows up.
set -uo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
src=$repo/sources
pstack=$src/michael-denyer/pstack-claude/plugins/pstack
herdr_skill=$src/faviann/agent-skills/skills/herdr-playbook
sessions=0 codex=0
for a in "$@"; do case $a in --sessions) sessions=1 ;; --codex) codex=1 ;; *) echo "unknown option $a" >&2; exit 2 ;; esac; done

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fails=0
check() { local name=$1; shift; if "$@"; then echo "ok   $name"; else echo "FAIL $name"; fails=$((fails + 1)); fi; }
refute() { local name=$1; shift; if "$@"; then echo "FAIL $name"; fails=$((fails + 1)); else echo "ok   $name"; fi; }

new_home() { local h=$T/$1; mkdir -p "$h/.claude/skills" "$h/.agents/skills" "$h/ws"; printf '%s' "$h"; }
cc() { local h=$1; shift; HOME=$h CLAUDE_CONFIG_DIR=$h/.claude claude "$@"; }
state() { cc "$1" plugin list --json | jq -r --arg id "$2" '[.[] | select(.id == $id)][0] | if . == null then "absent" else "\(.enabled) \(.version)" end'; }
is_state() { [[ $(state "$1" "$2") == "$3" ]]; }
version_of() { jq -r .version "$1/.claude-plugin/plugin.json"; }

inventory_matches() { # home id: plugin details equal the pinned pstack tree's skills, agents and hook events
  local skills agents hooks out
  skills=$(find "$pstack/skills" -mindepth 2 -maxdepth 2 -name SKILL.md | wc -l)
  agents=$(find "$pstack/agents" -maxdepth 1 -name '*.md' | wc -l)
  hooks=$(jq '.hooks | length' "$pstack/hooks/hooks.json")
  out=$(cc "$1" plugin details "$2")
  grep -q "Skills ($skills)" <<<"$out" && grep -q "Agents ($agents)" <<<"$out" && grep -q "Hooks ($hooks)" <<<"$out"
}

blocks_skills_dir() { # managed-settings file(s): true when they would block skills-dir plugins
  jq -se 'any(.[]; (.strictPluginOnlyCustomization // false) != false
              or ((.strictKnownMarketplaces // null) != null and (any(.strictKnownMarketplaces[]; .source == "skills-dir") | not)))' "$@" >/dev/null
}

h=$(new_home p1)
ln -s "$pstack" "$h/.claude/skills/pstack"
check "P1 linked plugin pack loads at the pin's version" is_state "$h" pstack@skills-dir "true $(version_of "$pstack")"
check "P1 no settings.json written" test ! -e "$h/.claude/settings.json"
h0=$(new_home p1-seed)
ln -s "$pstack/skills" "$h0/.claude/skills/pstack"
check "P1 seed: a dir without a manifest is not a plugin" is_state "$h0" pstack@skills-dir absent

check "P2 inventory matches the pin" inventory_matches "$h" pstack@skills-dir
h2=$(new_home p2-seed)
cp -r "$pstack" "$T/pstack-no-agents" && rm -r "$T/pstack-no-agents/agents"
ln -s "$T/pstack-no-agents" "$h2/.claude/skills/pstack"
refute "P2 seed: a pack missing its agents is caught" inventory_matches "$h2" pstack@skills-dir

cc "$h" plugin marketplace add "$src/michael-denyer/pstack-claude" --json >/dev/null
cc "$h" plugin install pstack@pstack-claude -y --json >/dev/null
check "P3 seed: marketplace pstack shadows the pack" is_state "$h" pstack@skills-dir "false unknown"
cc "$h" plugin uninstall pstack@pstack-claude --json >/dev/null
cc "$h" plugin marketplace remove pstack-claude --json >/dev/null
check "P3 uninstall hands the name back" is_state "$h" pstack@skills-dir "true $(version_of "$pstack")"
check "P3 marketplace remove clears extraKnownMarketplaces" \
  bash -c '[[ $(jq -c ".extraKnownMarketplaces // {}" "$1") == "{}" ]]' _ "$h/.claude/settings.json"

herdr=$T/herdr-pack
mkdir -p "$herdr/.claude-plugin" "$herdr/hooks"
printf '{"name": "herdr"}\n' >"$herdr/.claude-plugin/plugin.json"
cp "$herdr_skill/hooks/session-start.sh" "$herdr/hooks/session-start.sh"
cat >"$herdr/hooks/hooks.json" <<'EOF'
{"hooks": {"SessionStart": [{"matcher": "startup|resume|clear|compact",
  "hooks": [{"type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh\"", "timeout": 5}]}]}}
EOF
h4=$(new_home p4)
ln -s "$herdr_skill" "$h4/.claude/skills/herdr-playbook"
ln -s "$herdr" "$h4/.claude/skills/herdr"
check "P4 hook-only pack loads" is_state "$h4" herdr@skills-dir "true unknown"
check "P4 hook-only pack has one hook and no skills" bash -c 'o=$("$@"); grep -q "Hooks (1)" <<<"$o" && grep -q "Skills (0)" <<<"$o"' _ \
  env HOME="$h4" CLAUDE_CONFIG_DIR="$h4/.claude" claude plugin details herdr@skills-dir
check "P4 the plain skill dir is not a plugin" is_state "$h4" herdr-playbook@skills-dir absent

printf '{"strictKnownMarketplaces": [{"source": "github", "repo": "acme/tools"}]}' >"$T/m-block.json"
printf '{"strictPluginOnlyCustomization": true}' >"$T/m-lock.json"
printf '{"strictKnownMarketplaces": [{"source": "skills-dir"}], "permissions": {}}' >"$T/m-allow.json"
check "P5 seed: strictKnownMarketplaces without skills-dir blocks" blocks_skills_dir "$T/m-block.json"
check "P5 seed: strictPluginOnlyCustomization blocks" blocks_skills_dir "$T/m-lock.json"
refute "P5 an allowlist naming skills-dir does not block" blocks_skills_dir "$T/m-allow.json"

if ((sessions)); then
  creds() { cp "$HOME/.claude/.credentials.json" "$1/.claude/.credentials.json"; }
  session() { # home [env...]: one haiku session; prints the transcript path
    local h=$1; shift
    creds "$h"
    local -a gateway
    mapfile -t gateway < <(jq -r '.env // {} | to_entries[] | "\(.key)=\(.value)"' "$HOME/.claude/settings.json")
    (cd "$h/ws" && env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID "${gateway[@]}" HOME="$h" CLAUDE_CONFIG_DIR="$h/.claude" "$@" \
      claude -p --model haiku 'Reply ok.' >/dev/null 2>&1)
    rm -f "$h/.claude/.credentials.json"
    ls -t "$h"/.claude/projects/*/*.jsonl 2>/dev/null | head -1
  }
  attach() { [[ -s $1 ]] || { echo "no transcript" >&2; return 1; }; jq -r --arg t "$2" 'select(.attachment.type? == $t) | .attachment.content | tostring' "$1"; }
  hook_lines() { local a; a=$(attach "$1" hook_success) || { echo -1; return; }; grep -c "$2" <<<"$a" || true; }
  export -f attach hook_lines

  t=$(session "$h")
  check "S1 pack hook ran in a session" bash -c '(( $(hook_lines "$1" "You have pstack") >= 1 ))' _ "$t"
  check "S1 pack agents are listed" bash -c 'grep -q "pstack:poteto-agent" <<<"$(jq -r "select(.attachment.type? == \"agent_listing_delta\") | .attachment | tostring" "$1")"' _ "$t"

  t=$(session "$h4" HERDR_ENV=1 HERDR_PANE_ID=p9)
  check "S2 herdr primer injected exactly once" bash -c '(( $(hook_lines "$1" "runs in herdr pane p9") == 1 ))' _ "$t"
  check "S2 skill keeps its bare name" bash -c 'grep -q "^- herdr-playbook:" <<<"$(attach "$1" skill_listing)"' _ "$t"
  t=$(session "$h4")
  check "S2 no primer without herdr" bash -c '(( $(hook_lines "$1" "runs in herdr pane") == 0 ))' _ "$t"
  jq -n '{hooks: {SessionStart: [{matcher: "^(startup|resume|clear|compact|fork)$", hooks: [{type: "command",
    command: "f=\"$HOME/.claude/skills/herdr-playbook/hooks/session-start.sh\"; [ -r \"$f\" ] && bash \"$f\" || true", timeout: 5}]}]}}' \
    >"$h4/.claude/settings.json"
  t=$(session "$h4" HERDR_ENV=1 HERDR_PANE_ID=p9)
  check "S2 seed: the hand-wired entry doubles the primer" bash -c '(( $(hook_lines "$1" "runs in herdr pane p9") == 2 ))' _ "$t"
fi

if ((codex)); then
  codex_skills() { # home: skill names Codex reports
    local h=$1
    mkdir -p "$h/.codex" && cp "$HOME/.codex/auth.json" "$h/.codex/auth.json" && chmod 600 "$h/.codex/auth.json"
    (cd "$h/ws" && HOME=$h CODEX_HOME=$h/.codex codex exec --skip-git-repo-check -c model_reasoning_effort=low \
      'Use no tools. List the exact names of every skill available to you whose name contains "herdr", one per line, nothing else.' </dev/null 2>/dev/null)
    rm -f "$h/.codex/auth.json"
  }
  hc=$(new_home codex)
  ln -s "$herdr_skill" "$hc/.agents/skills/herdr-playbook"
  check "C1 Codex keeps the bare skill name" bash -c 'grep -qx "herdr-playbook" <<<"$1"' _ "$(codex_skills "$hc")"
  cp -r "$herdr_skill" "$T/hp-manifest" && mkdir -p "$T/hp-manifest/.claude-plugin"
  printf '{"name": "herdr-playbook"}\n' >"$T/hp-manifest/.claude-plugin/plugin.json"
  hs=$(new_home codex-seed)
  ln -s "$T/hp-manifest" "$hs/.agents/skills/herdr-playbook"
  check "C1 seed: a manifest in the skill dir renames it" bash -c 'grep -qx "herdr-playbook:herdr-playbook" <<<"$1"' _ "$(codex_skills "$hs")"
fi

echo "$fails failed"
exit $((fails > 0))
