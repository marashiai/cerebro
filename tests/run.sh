#!/usr/bin/env bash
# Offline core/native-fixture checks; never installs dependencies or calls models.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
for file in "$root/bin/cerebro" "$root/lib"/*.sh "$root/lib/commands"/*.sh; do
  bash -n "$file"
done
for name in task_lifecycle workspace pair_adapters durable_task role_models mcp_environment \
            retire_skills recall claude_hook native_input acp_launch jev_watch; do
  printf 'CHECK %s\n' "$name"
  python3 "$here/${name}_test.py"
done
python3 "$here/cerebro_mcp_unit.py"

# SDK tests use installed interpreters only. They remain optional on a stdlib-only host.
for sdk in acp mcp; do
  selected=""
  for candidate in "${CEREBRO_TEST_PYTHON:-python3}" /opt/homebrew/bin/python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import $sdk" >/dev/null 2>&1; then selected="$candidate"; break; fi
  done
  if [[ -z "$selected" ]]; then
    printf 'SKIP %s SDK integration (SDK unavailable)\n' "$sdk"
  elif [[ "$sdk" == acp ]]; then
    "$selected" "$here/acp/relay_test.py" "$root"
  else
    "$selected" "$here/cerebro_mcp_server.py" "$root"
  fi
done
