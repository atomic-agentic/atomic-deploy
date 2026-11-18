# SPDX-License-Identifier: Apache-2.0
#
# The probe actor for spikes 4 and 5. Codex's own bwrap is fetched and checked
# at build time, because an actor has no egress by default.
#
#   docker build -t localhost:5000/substrate-probe:<tag> .

# python:3.13-slim-trixie
FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS bwrap
ARG BWRAP_URL=https://github.com/openai/codex/releases/download/rust-v0.169.0/bwrap-x86_64-unknown-linux-musl.tar.gz
ARG BWRAP_SHA256=15fd5f2673d2e1c2f80e34e5108462fd014f5e8bbc7b0731f1ab23d1ff1ac140
RUN python3 -I -c 'import hashlib,io,sys,tarfile,urllib.request; \
url,sha=sys.argv[1:]; \
b=tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(url,timeout=60).read())).extractfile("bwrap-x86_64-unknown-linux-musl").read(); \
assert hashlib.sha256(b).hexdigest()==sha,"bwrap checksum mismatch"; \
open("/bwrap","wb").write(b)' "$BWRAP_URL" "$BWRAP_SHA256" \
    && chmod 0755 /bwrap

FROM python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=bwrap /bwrap /opt/probe/bwrap
COPY server.py /opt/probe/server.py
CMD ["python3", "-I", "-u", "/opt/probe/server.py"]
