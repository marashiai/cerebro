import sys, time

sys.dont_write_bytecode = True
from opencode_api import OpenCodeAPI

# Tiny control helper for the headless `opencode serve` a paired child runs
# under. Subcommands:
#   health <base_url>          -- exit 0 once the server answers, else 1
#   create <base_url> <title>  -- create a session, print its id

op = sys.argv[1] if len(sys.argv) > 1 else ""

if op == "health":
    api = OpenCodeAPI(sys.argv[2])
    for _ in range(80):
        try:
            with api.request(api.health_path, timeout=2):
                pass
            sys.exit(0)
        except Exception:
            time.sleep(0.2)
    sys.exit(1)

elif op == "create":
    api = OpenCodeAPI(sys.argv[2])
    try:
        sid = api.create(sys.argv[3], sys.argv[4])
    except Exception:
        sys.exit(1)
    if not sid:
        sys.exit(1)
    print(sid)

elif op == "gate":
    import json
    api = OpenCodeAPI(sys.argv[2])
    try:
        with api.request('/api/rpc/cerebro/ready', {'input': {}}, timeout=10) as response:
            ready = json.load(response)['output']
        sys.exit(0 if ready is True else 1)
    except Exception as error:
        print('OpenCode role gate: ' + str(error), file=sys.stderr)
        sys.exit(1)

else:
    sys.exit(2)
