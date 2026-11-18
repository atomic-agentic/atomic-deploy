# SPDX-License-Identifier: Apache-2.0
#
# The Codex runner for the end-to-end spike: Codex rust-v0.169.0 and its
# digest-checked bwrap (from the probe image), exec-server and git (from the
# exec-server image), and the probe on port 80 for the wake-up probe. The
# same image serves both placements: Codex runs here (harness mode), or only
# exec-server does and Codex runs elsewhere (execution-only).
#
#   docker build --build-context codex=docker-image://localhost:5000/substrate-codex-probe:<tag> \
#     --build-context exec=docker-image://localhost:5000/substrate-exec-server:<tag> \
#     -t localhost:5000/substrate-codex-runner:<tag> .
FROM exec
COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex
RUN ln -sf /opt/probe/bwrap /usr/local/bin/bwrap
