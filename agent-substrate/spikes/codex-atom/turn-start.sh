#!/bin/sh
# Starts the Codex turn detached from the caller's session, as atomic runner
# would own it, so closing the exec-server connection does not stop it.
export CODEX_HOME=/work/codex-home AGW_KEY=placeholder
cd /work/task
setsid -f sh -c 'codex exec --json -o /work/run/last-message.txt \
  "Run ./slow.sh exactly once and wait until it finishes (it takes about a minute), then reply with the last line it printed." \
  > /work/run/events.jsonl 2> /work/run/stderr.txt; echo $? > /work/run/codex-exit'
echo "started at $(date -u +%T)"
