#!/usr/bin/env bash
# Claude's synchronous UserPromptSubmit hook preserves its JSON input exactly.
set -uo pipefail
hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$hook_dir/../python/native_input.py" claude
