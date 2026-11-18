# SPDX-License-Identifier: Apache-2.0
#
# The probe actor plus Codex rust-v0.169.0 (atomic's pinned Codex), for spike
# 5's follow-up: Codex's own sandbox command inside a gVisor actor.
#
#   docker build --build-context probe=docker-image://localhost:5000/substrate-probe:<tag> -t localhost:5000/substrate-codex-probe:<tag> .

# python:3.13-slim-trixie
FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS codex
ARG CODEX_URL=https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-x86_64-unknown-linux-musl.tar.gz
# The release asset's digest, as GitHub publishes it.
ARG CODEX_SHA256=9af4d751fce183faedea629437d5252dbf458eeb04130cf4f97be844621ea985
RUN python3 -I -c 'import hashlib,io,sys,tarfile,urllib.request; \
url,sha=sys.argv[1:]; data=urllib.request.urlopen(url,timeout=300).read(); \
assert hashlib.sha256(data).hexdigest()==sha,"codex archive checksum mismatch"; \
t=tarfile.open(fileobj=io.BytesIO(data)); m=t.getmember("codex-x86_64-unknown-linux-musl"); \
open("/codex","wb").write(t.extractfile(m).read())' "$CODEX_URL" "$CODEX_SHA256" \
    && chmod 0755 /codex

FROM probe
COPY --from=codex /codex /usr/local/bin/codex
# Codex finds its bwrap on PATH; the probe image's copy is release-verified.
RUN ln -s /opt/probe/bwrap /usr/local/bin/bwrap && mkdir -p /work /var/lib/codex-home
# Not under /tmp: an actor's /tmp is a fresh tmpfs.
ENV CODEX_HOME=/var/lib/codex-home
