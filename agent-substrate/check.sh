#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check the deployment end to end, after up.sh. Each check is one of the
# spikes' results (findings.md), reproduced on what up.sh built:
#
#   harness         Codex in a runner actor fixes a failing test, its sandbox
#                   on, its model calls through agentgateway on an actor JWT
#                   (spike 10)
#   quiescence      that actor is not suspended while its fix is uncommitted;
#                   once committed, it is, after the idle period (spike 12)
#   execution-only  Codex on this host, in the runner image, fixes the same
#                   test with its commands in a second actor (spike 10)
#   gate            the kagent bridge pauses at a gate, kagent suspends it, the
#                   answer resumes it, and the atomic serve stand-in receives
#                   the decision with an actor JWT for kagent's session (spike 6)
#
#   ./check.sh           # deletes its actors afterwards
#   ./check.sh --keep    # leaves them, suspended, for a look
#
# Exits 0 when every check passes. Uses ports 18183 and 18210-18222 on 127.0.0.1.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
export MODEL_PORT=${MODEL_PORT:-13305}
export MODEL_NAME=${MODEL_NAME:-Qwen3.6-35B-A3B-MTP-GGUF-UD-Q4_K_XL}
export ATESPACE=${ATESPACE:-atomic}
MODEL_PROVIDER_KEY=${MODEL_PROVIDER_KEY:-unused}
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/k3s.yaml}
keep=0; [[ ${1:-} == --keep ]] && keep=1

ate() { kubectl-ate "$@" 2> >(grep -v '^Warning: no --token-file' >&2); }
render() { local f=$1; shift; envsubst "$(printf '${%s} ' "$@")" < "$f"; }
passed=(); failed=()
pass() { echo "PASS $1: $2"; passed+=("$1"); }
fail() { echo "FAIL $1: $2"; failed+=("$1"); }
work=$(mktemp -d)
pids=()
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; wait "$p" 2>/dev/null; done
    if [[ $keep -eq 0 ]]; then
        for a in check-harness check-exec; do ate delete actor "$a" -a "$ATESPACE" >/dev/null 2>&1; done
    fi
    rm -rf "$work"
}
trap cleanup EXIT

# A command in an actor through its exec-server; prints the command's output.
X() {
    local port=$1; shift
    timeout "${T:-120}" python3 -I tools/exec-client.py "ws://127.0.0.1:$port/" "$@" 2>&1 \
        | sed -n '/^output: /,$p' | sed '1s/^output: //'
}
# A fresh actor from the runner template, proxied: <name> <exec port> <http port>.
actor() {
    ate delete actor "$1" -a "$ATESPACE" >/dev/null 2>&1
    ate create actor "$1" -a "$ATESPACE" --template runner >/dev/null || return 1
    kubectl-ate proxy actor "$1" -a "$ATESPACE" "$2:8080" "$3:80" >"$work/proxy-$1.log" 2>&1 &
    pids+=($!)
    for _ in $(seq 120); do curl -fs -m 5 -o /dev/null "http://127.0.0.1:$3/readyz" && return 0; sleep 1; done
    return 1
}
# The task repository, Codex's configuration and the prompt, in an actor.
setup() {
    local port=$1 config=$2
    X "$port" --env CODEX_HOME=/work/codex-home --argv sh -c "$(cat check/task.sh)
mkdir -p /work/run \$CODEX_HOME
cat > \$CODEX_HOME/config.toml <<'TOML'
$config
TOML
cat > /work/run/prompt.txt <<'TXT'
$(cat check/prompt.txt)
TXT
echo setup-done" | tail -1
}

runner_image=$(ate get actor-template runner -a "$ATESPACE" -o json 2>/dev/null \
    | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["containers"][0]["image"])' 2>/dev/null)
[[ -n $runner_image ]] || { echo "No runner template in atespace $ATESPACE; run ./up.sh first." >&2; exit 1; }
echo "runner image: $runner_image"

echo; echo "== harness"
if ! actor check-harness 18210 18211; then
    fail harness "the actor did not become ready"
else
    ate create egress-policy check-harness -a "$ATESPACE" -f actors/runner-egress.yaml >/dev/null
    out=$(setup 18210 "$(render codex/harness.toml MODEL_NAME)")
    [[ $out == setup-done ]] || echo "setup: $out"
    s=$(date +%s)
    out=$(T=900 X 18210 --env CODEX_HOME=/work/codex-home --argv sh -c '
cd /work/task && AGW_KEY=placeholder timeout 600 codex exec --json -o /work/run/last-message.txt \
  "$(cat /work/run/prompt.txt)" > /work/run/events.jsonl 2> /work/run/stderr.txt
echo "codex-exit=$?"; python3 test_calc.py; git status --short')
    echo "$out" | sed 's/^/  /'
    if grep -qx 'codex-exit=0' <<<"$out" && grep -qx ok <<<"$out"; then
        pass harness "Codex fixed the test in the actor in $(( $(date +%s) - s )) s"
    else
        fail harness "see the output above; Codex's stderr is /work/run/stderr.txt in check-harness (--keep)"
    fi
fi

echo; echo "== quiescence"
if [[ " ${failed[*]} " == *" harness "* ]]; then
    fail quiescence "skipped: the harness check failed"
else
    ATESPACE=$ATESPACE tools/suspend-when-quiescent.sh check-harness 18212 5 120 >"$work/driver.log" 2>&1 &
    driver=$!
    sleep 12   # the fix is still uncommitted: the driver must hold off
    X 18210 --argv sh -c 'cd /work/task && git add -A && git -c user.email=check@example.invalid -c user.name=check commit -qm "fix add" && echo committed' >/dev/null
    wait "$driver"; rc=$?
    sed 's/^/  /' "$work/driver.log"
    if [[ $rc -eq 0 ]] && grep -q 'busy: .*worktree has uncommitted' "$work/driver.log"; then
        pass quiescence "held off while the fix was uncommitted, then suspended"
    else
        fail quiescence "driver exit $rc"
    fi
fi

echo; echo "== execution-only"
if ! actor check-exec 18220 18221; then
    fail execution-only "the actor did not become ready"
else
    out=$(setup 18220 "")
    [[ $out == setup-done ]] || echo "setup: $out"
    cfg=$work/exec-only && mkdir -p "$cfg"
    render codex/exec-only.toml MODEL_NAME MODEL_PORT > "$cfg/config.toml"
    cp codex/environments.toml check/prompt.txt "$cfg/"
    s=$(date +%s)
    # Codex checks -C on its own filesystem first, so the directory exists
    # here, empty; the files exist only in the actor.
    docker run --rm --network host -e LEMONADE_KEY="$MODEL_PROVIDER_KEY" -v "$cfg:/cfg:ro,z" \
        --entrypoint sh "$runner_image" -c '
mkdir -p /tmp/codex-home /work/task && cp /cfg/config.toml /cfg/environments.toml /tmp/codex-home/
CODEX_HOME=/tmp/codex-home timeout 600 codex exec --json --skip-git-repo-check -C /work/task \
  "$(cat /cfg/prompt.txt)" > /tmp/events.jsonl 2> /tmp/stderr.txt
rc=$?; echo "codex-exit=$rc"; [ $rc -eq 0 ] || tail -5 /tmp/stderr.txt' >"$work/exec-only.log" 2>&1
    out=$(X 18220 --argv sh -c 'cd /work/task && python3 test_calc.py')
    sed 's/^/  /' "$work/exec-only.log"; echo "  in the actor: $out"
    if grep -qx 'codex-exit=0' "$work/exec-only.log" && [[ $out == ok ]]; then
        pass execution-only "Codex on the host fixed the test in the actor in $(( $(date +%s) - s )) s"
    else
        fail execution-only "see the output above"
    fi
fi

echo; echo "== gate"
kubectl -n kagent wait agent/atomic-bridge --for=condition=Ready --timeout=180s >/dev/null
kubectl -n kagent port-forward svc/kagent-controller 18183:8083 >"$work/pf-kagent.log" 2>&1 &
pids+=($!)
for _ in $(seq 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:18183/ && break; sleep 1; done
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 -I tools/kagent-gate.py http://127.0.0.1:18183 kagent/atomic-bridge --wait 20 >"$work/gate.log" 2>&1; rc=$?
sed -n '1,/^bridge says/p' "$work/gate.log" | cut -c1-160 | sed 's/^/  /'
seen=$(kubectl -n atomic logs deploy/atomic-serve --since-time="$since" | grep '^request ' | tail -1 | cut -d' ' -f2- \
    | python3 -I -c 'import json,sys; r=json.load(sys.stdin); p=(r.get("jwt") or {}).get("payload",{})
print(p.get("sub",""), ",".join(p.get("aud",[])), r["body"]["gate"])' 2>/dev/null)
echo "  atomic serve stand-in saw: ${seen:-nothing}"
if [[ $rc -eq 0 && $seen == "actor/kagent/session-"*" atomic-serve gate-send-reply" ]]; then
    pass gate "decided through kagent; the decision arrived with kagent's actor JWT"
else
    fail gate "kagent-gate exit $rc"
fi

echo
echo "${#passed[@]} passed${passed:+: ${passed[*]}}; ${#failed[@]} failed${failed:+: ${failed[*]}}"
[[ ${#failed[@]} -eq 0 ]]
