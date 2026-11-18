#!/bin/sh
# Spike atomic-1b7t.22: a task whose turn lasts about a minute, so an actor
# can be suspended while Codex is busy. CODEX_HOME is on the durable /work so
# a volumes-only suspend keeps Codex's state; only the turn is at risk.
set -eu
rm -rf /work/task /work/codex-home /work/run && mkdir -p /work/task /work/codex-home /work/run
cd /work/task
cat > slow.sh <<'SH'
#!/bin/sh
echo "slow.sh started at $(date -u +%T)"
sleep 60
echo "slow.sh finished at $(date -u +%T)" | tee /work/task/marker
SH
chmod +x slow.sh
git init -q -b main && git -c user.email=spike@example.com -c user.name=spike add -A \
  && git -c user.email=spike@example.com -c user.name=spike commit -q -m "a slow script"
echo setup-done
