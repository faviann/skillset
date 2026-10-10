#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
selector="$script_dir/select-skills.py"
local_bin="$HOME/.local/bin"
mkdir -p -- "$local_bin"
for path in "$local_bin/select-skills" "$local_bin/select-skills.lock"; do
  if [ -e "$path" ] && [ ! -L "$path" ]; then
    printf 'error: refusing to replace existing file or directory: %s\n' "$path" >&2
    exit 1
  fi
done
ln -sfn -- "$selector" "$local_bin/select-skills"
# uv looks beside the invoked path for its lock. Setup's sync also caches
# every package needed by the installed command's separate environment.
ln -sfn -- "$selector.lock" "$local_bin/select-skills.lock"
