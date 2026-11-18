# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# A spike stand-in for the design's `agent` runner: Codex in a Lambda function,
# with a loopback shim in front of Bedrock. Not atomic's runner, which does not
# exist yet; see README.md.
FROM public.ecr.aws/lambda/python:3.13@sha256:eac388575a9ff5c116d32b9620675341f998def5fe224fc3752186986afa9a4a

# Codex rust-v0.169.0, the revision atomic pins. The tarball's checksum is the
# one whose binary was verified against the release workflow's Sigstore
# certificate; the binary's own checksum is checked after unpacking.
ADD --checksum=sha256:9af4d751fce183faedea629437d5252dbf458eeb04130cf4f97be844621ea985 \
    https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-x86_64-unknown-linux-musl.tar.gz \
    /tmp/codex.tar.gz
RUN python3 - <<'PY'
import hashlib, os, tarfile
tarfile.open("/tmp/codex.tar.gz").extract("codex-x86_64-unknown-linux-musl", "/tmp", filter="data")
data = open("/tmp/codex-x86_64-unknown-linux-musl", "rb").read()
assert hashlib.sha256(data).hexdigest() == "b452ca20f838a8855a88a41050ee4a694033fff4ea3ecdad5633e6e0e8d13f2b", "codex checksum"
os.makedirs("/opt/codex", exist_ok=True)
open("/opt/codex/codex", "wb").write(data); os.chmod("/opt/codex/codex", 0o755)
os.remove("/tmp/codex.tar.gz"); os.remove("/tmp/codex-x86_64-unknown-linux-musl")
PY

COPY handler.py shim.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.handler"]
