# SPDX-License-Identifier: Apache-2.0
#
# The runner: what runs in an atom's actor, in either placement.
#
#   - Codex rust-v0.169.0 (atomic's pinned Codex) and its own bwrap, which
#     Codex checks by digest, for harness mode: Codex runs here.
#   - Codex's standalone exec-server on port 8080, for execution-only
#     placement: Codex runs in the control plane and its commands run here.
#   - git, for the attempt's worktree.
#   - runner.py on port 80: the wake-up probe and the quiescence report, a
#     stand-in for `atomic runner`.
#
# Every release asset is checked against the digest GitHub publishes for it,
# at build time, because an actor has no egress by default.
#
#   docker build -t localhost:5000/atomic-runner:<tag> images/runner

# python:3.13-slim-trixie
FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS fetch
ARG RELEASE=https://github.com/openai/codex/releases/download/rust-v0.169.0
ARG CODEX_SHA256=9af4d751fce183faedea629437d5252dbf458eeb04130cf4f97be844621ea985
ARG EXEC_SERVER_SHA256=96709e8cecc01c97369ba24d2adac7a528b3580c22f8966fa4cda701048abcde
ARG BWRAP_SHA256=15fd5f2673d2e1c2f80e34e5108462fd014f5e8bbc7b0731f1ab23d1ff1ac140
# Each archive is checked whole, then its one binary extracted. The bwrap
# archive's own member is checked too: that digest is what Codex verifies.
RUN python3 -I - "$RELEASE" "$CODEX_SHA256" "$EXEC_SERVER_SHA256" "$BWRAP_SHA256" <<'EOF'
import hashlib, io, os, sys, tarfile, urllib.request
release, codex, exec_server, bwrap = sys.argv[1:]
def fetch(asset, sha):
    data = urllib.request.urlopen(f"{release}/{asset}", timeout=300).read()
    assert hashlib.sha256(data).hexdigest() == sha, f"{asset}: checksum mismatch"
    return tarfile.open(fileobj=io.BytesIO(data))
def write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o755)
t = fetch("codex-x86_64-unknown-linux-musl.tar.gz", codex)
write("/codex", t.extractfile("codex-x86_64-unknown-linux-musl").read())
t = fetch("codex-exec-server-x86_64-unknown-linux-musl.tar.gz", exec_server)
write("/codex-exec-server", t.extractfile([m for m in t.getmembers() if m.isfile()][0]).read())
b = urllib.request.urlopen(f"{release}/bwrap-x86_64-unknown-linux-musl.tar.gz", timeout=300).read()
b = tarfile.open(fileobj=io.BytesIO(b)).extractfile("bwrap-x86_64-unknown-linux-musl").read()
assert hashlib.sha256(b).hexdigest() == bwrap, "bwrap: checksum mismatch"
write("/bwrap", b)
EOF

FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /work
COPY --from=fetch /codex /codex-exec-server /bwrap /usr/local/bin/
COPY runner.py /opt/atomic/runner.py
# Codex's state on the actor's durable directory: a volumes-only suspend loses
# the root filesystem (findings.md, spike 10), and the quiescence report
# refuses a CODEX_HOME anywhere else. Not under /tmp, a fresh tmpfs in an actor.
ENV CODEX_HOME=/work/codex-home
CMD ["sh", "-c", "codex-exec-server --listen ws://0.0.0.0:8080 & exec python3 -I -u /opt/atomic/runner.py"]
