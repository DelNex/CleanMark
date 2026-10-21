#!/usr/bin/env bash
# Convenient Linux entry point.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/start-linux.sh" "$@"
