# SPDX-License-Identifier: Apache-2.0
#
# The probe actor plus Codex rust-v0.169.0's standalone exec-server, for spike
# 4's follow-up: a real exec-server session through Substrate's router. The
# probe stays on port 80 for the wake-up probe; exec-server listens on 8080.
#
#   docker build --build-context probe=docker-image://localhost:5000/substrate-probe:<tag> \
#     -t localhost:5000/substrate-exec-server:<tag> .

# python:3.13-slim-trixie
FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS exec
ARG URL=https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-exec-server-x86_64-unknown-linux-musl.tar.gz
# The release asset's digest, as GitHub publishes it.
ARG SHA256=96709e8cecc01c97369ba24d2adac7a528b3580c22f8966fa4cda701048abcde
RUN python3 -I -c 'import hashlib,io,sys,tarfile,urllib.request; \
url,sha=sys.argv[1:]; data=urllib.request.urlopen(url,timeout=300).read(); \
assert hashlib.sha256(data).hexdigest()==sha,"exec-server archive checksum mismatch"; \
t=tarfile.open(fileobj=io.BytesIO(data)); print([m.name for m in t.getmembers()]); \
m=[m for m in t.getmembers() if m.isfile()][0]; open("/codex-exec-server","wb").write(t.extractfile(m).read())' "$URL" "$SHA256" \
    && chmod 0755 /codex-exec-server

FROM probe
COPY --from=exec /codex-exec-server /usr/local/bin/codex-exec-server
# git, for spike 7's realistic worktree: a clone made inside the actor.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /work /var/lib/codex-home
ENV CODEX_HOME=/var/lib/codex-home
CMD ["sh", "-c", "codex-exec-server --listen ws://0.0.0.0:8080 & exec python3 -I -u /opt/probe/server.py"]
