#!/usr/bin/env bash
set -euo pipefail

initial_path=$PATH
if ! command -v git >/dev/null; then
  printf 'error: git is required.\n' >&2
  exit 1
fi
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  printf 'error: Python 3.11 or newer is required.\n' >&2
  exit 1
fi
if ! command -v uv >/dev/null; then
  printf 'Install uv now? [y/N] '
  read -r answer || answer=
  case "$answer" in
    [yY]|[yY][eE][sS])
      curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
      export PATH="$HOME/.local/bin:$PATH"
      ;;
    *)
      printf 'uv is required; setup stopped.\n' >&2
      exit 1
      ;;
  esac
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
selector="$PWD/scripts/select-skills.py"
git submodule update --init --recursive --checkout
uv sync --locked --script "$selector"

local_bin="$HOME/.local/bin"
mkdir -p -- "$local_bin"
for path in "$local_bin/select-skills" "$local_bin/select-skills.lock"; do
  if [[ -e "$path" && ! -L "$path" ]]; then
    printf 'error: refusing to replace existing file or directory: %s\n' "$path" >&2
    exit 1
  fi
done
ln -sfn -- "$selector" "$local_bin/select-skills"
# uv looks beside the invoked path for its lock. The earlier sync also caches
# every package needed by the installed command's separate environment.
ln -sfn -- "$selector.lock" "$local_bin/select-skills.lock"

case ":$initial_path:" in
  *":$local_bin:"*) ;;
  *) printf 'Warning: %s is not on PATH. Add it to PATH to use select-skills.\n' "$local_bin" >&2 ;;
esac
if [[ -t 0 && -t 1 ]]; then
  exec "$local_bin/select-skills"
fi
printf 'Ready. Run: select-skills\n'
