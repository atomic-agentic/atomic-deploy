#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The spike's stand-in control plane (an ECS Fargate task): the Mantle shim,
# the rendezvous, and Codex in execution-only placement, whose only
# environment is the exec-server a Lambda function dials out from.
set -eu
ENV_ID="${ENV_ID:-spike}"
REGION="${AWS_REGION:-us-west-2}"
export HOME=/tmp CODEX_HOME=/tmp/codex
mkdir -p "$CODEX_HOME"

python3 /app/shim_main.py "$REGION" &
python3 /app/rendezvous.py &

status() { python3 -c 'import json,sys,urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8765/status", timeout=5).read().decode())' 2>/dev/null || true; }

echo "control plane: waiting for the Lambda function's exec-server to dial in"
i=0
until status | grep -q '"executor_connected_at_s": [0-9]'; do
    i=$((i + 1)); [ "$i" -lt 300 ] || { echo "no executor after 300 s"; status; exit 1; }
    sleep 1
done
echo "control plane: executor connected: $(status)"

cat > "$CODEX_HOME/config.toml" <<EOF
model_provider = "amazon-bedrock"
model = "openai.gpt-oss-120b"
approval_policy = "never"
# The Lambda function's microVM is the boundary; Codex's sandbox cannot start there.
default_permissions = ":danger-full-access"
web_search = "disabled"
check_for_update_on_startup = false

[analytics]
enabled = false

[feedback]
enabled = false

[history]
persistence = "none"

[features]
multi_agent = false
unified_exec_tty = false

[model_providers.amazon-bedrock]
base_url = "http://127.0.0.1:8080/v1"
aws = { region = "$REGION" }
EOF
cat > "$CODEX_HOME/environments.toml" <<EOF
# The Lambda function, reached through the rendezvous, is Codex's only environment.
default = "lambda"
include_local = false

[[environments]]
id = "lambda"
url = "ws://127.0.0.1:8765/harness/$ENV_ID"
connect_timeout_sec = 60
EOF

# Codex checks the working directory on its own filesystem before it uses the
# remote environment, so the path exists here too, empty.
mkdir -p /tmp/work/task
PROMPT="The test in test_calc.py fails. Fix calc.py so that \`python3 test_calc.py\` prints ok, then run the test to confirm. Do not change test_calc.py."
start=$(date +%s)
set +e
codex exec --skip-git-repo-check -C /tmp/work/task --json "$PROMPT" > /tmp/events.jsonl 2> /tmp/codex.err
code=$?
set -e
echo "control plane: codex exit $code after $(( $(date +%s) - start )) s"
python3 - <<'PY'
import json
types, commands, usage, last = {}, [], None, None
for line in open("/tmp/events.jsonl"):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    types[e.get("type")] = types.get(e.get("type"), 0) + 1
    item = e.get("item") or {}
    if e.get("type") == "item.completed" and item.get("type") == "command_execution":
        commands.append({"command": item.get("command"), "exit": item.get("exit_code"), "output": (item.get("aggregated_output") or "")[-200:]})
    if e.get("type") == "item.completed" and item.get("type") == "agent_message":
        last = item.get("text")
    if e.get("type") == "turn.completed":
        usage = e.get("usage")
print("RESULT " + json.dumps({"event_types": types, "commands": commands, "usage": usage, "final_message": last}))
PY
tail -5 /tmp/codex.err | sed 's/^/codex stderr: /'
python3 -c 'import urllib.request; urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765/done", method="POST"), timeout=5)'
echo "RENDEZVOUS $(status)"
sleep 10
