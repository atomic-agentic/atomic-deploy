#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# The control plane's side of quiescence, until atomic serve does it: suspends
# an actor only when its runner reports it quiescent (GET /quiescence) and has
# stayed so for the idle period (findings.md, spike 12).
#
#   tools/suspend-when-quiescent.sh <actor> <local port> [idle s] [max wait s]
#
# Exit 0 suspended, 2 still busy at the deadline (not suspended), 3 the turn
# was lost (not suspended: atomic fails or resumes the attempt instead).
# Prints one line per change of state. Run with KUBECONFIG set.
set -u
A=$1; PORT=$2; IDLE=${3:-10}; MAX=${4:-300}; ATESPACE=${ATESPACE:-atomic}
kubectl-ate proxy actor "$A" -a "$ATESPACE" "$PORT:80" >/dev/null 2>&1 & P=$!
trap 'kill $P 2>/dev/null; wait $P 2>/dev/null' EXIT
for _ in $(seq 120); do curl -s -o /dev/null "http://127.0.0.1:$PORT/readyz" && break; sleep 1; done

t0=$(date +%s); last=""
while :; do
    line=$(curl -s -m 30 "http://127.0.0.1:$PORT/quiescence" | python3 -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("unreachable 0 -"); sys.exit()
print(d["state"], d["quiescent_for_s"], "; ".join(d["reasons"]) or "-")')
    state=${line%% *}; rest=${line#* }; for_s=${rest%% *}; why=${rest#* }
    now=$(( $(date +%s) - t0 ))
    if [ "$state $why" != "$last" ]; then echo "t=${now}s $state: $why"; last="$state $why"; fi
    case $state in
    quiescent)
        if python3 -I -c "import sys; sys.exit(0 if float('$for_s') >= $IDLE else 1)"; then
            s=$(date +%s.%N)
            kubectl-ate suspend actor "$A" -a "$ATESPACE" --mode=sync >/dev/null 2>&1
            echo "t=${now}s quiescent for ${for_s} s >= ${IDLE} s: suspended (sync) in $(python3 -I -c "print(round($(date +%s.%N)-$s,2))") s"
            exit 0
        fi ;;
    lost)
        echo "t=${now}s turn lost: not suspending; atomic fails or resumes the attempt"
        exit 3 ;;
    esac
    if [ "$now" -ge "$MAX" ]; then echo "t=${now}s still $state at the deadline: not suspended"; exit 2; fi
    sleep 3
done
