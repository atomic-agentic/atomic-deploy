#!/usr/bin/env bash
# Spike 12: the runner's quiescence report and a control plane that suspends
# only on it. Run from agent-substrate/spikes with KUBECONFIG set.
#   Q1 (q-mem): a normal attempt; suspend waits for the turn and for its output
#      to be committed.
#   Q2 (q-mem): a background process left behind; no suspend until it is gone.
#   Q3 (q-vol): a suspend forced mid-turn (ignoring quiescence) at volumes
#      fidelity; the runner then reports the turn lost.
set -u
LOG=$(mktemp); trap 'rm -f "$LOG"' EXIT
X() { timeout 200 python3 -I tools/exec-client.py "ws://127.0.0.1:$1/" --argv sh -c "$2" 2>&1 | sed -n '/^output/,$p' | sed 's/^output: //'; }
proxy() { kubectl-ate proxy actor "$1" -a spikes "$2:8080" "$(( $2 + 1 )):80" >/dev/null 2>&1 & echo $!; }
up() { for _ in $(seq 120); do curl -s -o /dev/null "http://127.0.0.1:$1/readyz" && return; sleep 1; done; }
SETUP="$(cat codex-atom/turn-setup.sh)
cat > /work/codex-home/config.toml <<'TOML'
$(cat codex-atom/harness-config.toml)
TOML"

echo "=== Q1: a normal attempt (q-mem, memory fidelity)"
P=$(proxy q-mem 18260); up 18261
X 18260 "$SETUP" | tail -1
tools/suspend-when-quiescent.sh q-mem 18262 10 400 > "$LOG" 2>&1 & D=$!
sleep 4; X 18260 "$(cat codex-atom/turn-start.sh)"
for _ in $(seq 60); do sleep 8; X 18260 "cat /work/run/codex-exit 2>/dev/null" | grep -q '^0$' && break; done
echo "codex finished; the runner stand-in commits the output 20 s later"; sleep 20
X 18260 "cd /work/task && git add -A && git -c user.email=spike@example.com -c user.name=spike commit -qm 'slow.sh output' && git log --oneline | head -2"
wait $D; echo "driver exit $?"; cat "$LOG"
kill $P; wait $P 2>/dev/null

echo "=== Q2: a background process left behind (q-mem)"
P=$(proxy q-mem 18260); up 18261
X 18260 "setsid -f sleep 120; echo left sleep 120 running"
tools/suspend-when-quiescent.sh q-mem 18262 5 30; echo "driver exit $?"
X 18260 "python3 -I -c 'import os,signal; [os.kill(int(p),signal.SIGTERM) for p in os.listdir(\"/proc\") if p.isdigit() and b\"sleep\\x00120\" in open(f\"/proc/{p}/cmdline\",\"rb\").read()]'; echo killed it"
tools/suspend-when-quiescent.sh q-mem 18262 5 60; echo "driver exit $?"
kill $P; wait $P 2>/dev/null

echo "=== Q3: a suspend that ignored quiescence (q-vol, volumes fidelity)"
P=$(proxy q-vol 18270); up 18271
X 18270 "$SETUP" | tail -1
X 18270 "$(cat codex-atom/turn-start.sh)"
for _ in $(seq 30); do sleep 3; curl -s "http://127.0.0.1:18271/quiescence" | grep -q command_execution && break; done
echo "busy, as reported: $(curl -s http://127.0.0.1:18271/quiescence | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(d["state"], "|", "; ".join(d["reasons"]))')"
kubectl-ate suspend actor q-vol -a spikes --mode=sync >/dev/null 2>&1; echo "forced a suspend mid-turn"; sleep 10
tools/suspend-when-quiescent.sh q-vol 18272 5 30; echo "driver exit $?"
kill $P; wait $P 2>/dev/null
kubectl-ate suspend actor q-vol -a spikes --mode=sync >/dev/null 2>&1
