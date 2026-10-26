#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/start.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/start.py" "$@"
else
    printf '%s\n' 'Blog Studio requires Python 3.10 or newer. Install Python and run this file again.' >&2
    exit 1
fi
