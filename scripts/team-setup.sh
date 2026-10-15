#!/bin/sh
# Run the ecadlib team server with Docker and HTTPS: same as `python3 -m ecadlib team setup` from this checkout.
# Needs Docker (with the compose plugin) and Python 3.10+; nothing to pip install. Options: --help.
set -eu
cd "$(dirname "$0")/.."
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" -m ecadlib team setup "$@"
