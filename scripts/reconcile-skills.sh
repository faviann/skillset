#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
python3 "$script_dir/reconcile_skills.py" "$@"
# Link only after the reconciler has accepted this as the live checkout.
[ "$#" -eq 0 ] || exit 0
exec "$script_dir/link-selector.sh"
