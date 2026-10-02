#!/bin/sh
# Project-only GitHub entry point. Never invoke gh's keychain login flow.
set -eu
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/github_auth.py" "$@"
