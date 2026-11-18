#!/usr/bin/env bash
# Spike atomic-1b7t.22: suspend an actor while Codex is mid-turn.
#   tools/turn-spike.sh <actor> <local port base>
# Run from agent-substrate/spikes with KUBECONFIG set.
set -u
A=$1; PORT=$2; X="python3 -I tools/exec-client.py ws://127.0.0.1:$PORT/"
kubectl-ate proxy actor "$A" -a spikes "$PORT:8080" "$((PORT+1)):80" >/dev/null 2>&1 & P=$!
up() { for _ in $(seq 120); do curl -s -o /dev/null "http://127.0.0.1:$((PORT+1))/readyz" && return; sleep 1; done; }
run() { timeout 120 $X --argv sh -c "$1" 2>&1 | sed -n '/^output/,$p' | sed 's/^output: //'; }
state() { run "python3 -I -c '$(sed "s/'/'\"'\"'/g" codex-atom/turn-state.py)'"; }
up
echo "== setup"; run "$(cat codex-atom/turn-setup.sh)
cat > /work/codex-home/config.toml <<'TOML'
$(cat codex-atom/harness-config.toml)
TOML"
echo "== start"; run "$(cat codex-atom/turn-start.sh)"
for _ in $(seq 30); do s=$(state); echo "$s" | grep -q "command_execution" && break; sleep 3; done
sleep 10
echo "== busy, just before the suspend"; state
t=$(date +%s.%N); kubectl-ate suspend actor "$A" -a spikes --mode=sync >/dev/null 2>&1
echo "== suspended (sync) in $(python3 -I -c "print(round($(date +%s.%N)-$t,2))") s at $(date -u +%T); waiting 20 s"; sleep 20
t=$(date +%s.%N); curl -s -o /dev/null -m 120 "http://127.0.0.1:$((PORT+1))/readyz"
echo "== resumed in $(python3 -I -c "print(round($(date +%s.%N)-$t,2))") s"; state
for _ in $(seq 36); do s=$(state); echo "$s" | grep -q "codex-exit: absent" || break; sleep 5; done
echo "== final"; state
echo "== stderr tail"; run "tail -5 /work/run/stderr.txt"
kill $P; wait $P 2>/dev/null
kubectl-ate suspend actor "$A" -a spikes --mode=sync >/dev/null 2>&1
