# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# atomic's runtime image, built from a release archive rather than from
# source. stage.sh puts one archive and its .sha256 in dist/; the build checks
# the archive against it. A checksum shows the archive arrived intact, not who
# made it: verify a downloaded release's signature before staging it.

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

# The binary links OpenSSL (libssl3t64). git is for the commits atomic makes
# outside the sandbox; bubblewrap is the sandbox Codex runs atom commands in;
# tini forwards signals so a stop lets the durable runtime shut down cleanly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git bubblewrap libssl3t64 tini \
    && rm -rf /var/lib/apt/lists/*

# The archive is bind-mounted for this step only, so it is not kept in a
# layer. The version check runs with a throwaway ATOMIC_HOME because atomic
# creates its state directory before it reads its arguments.
RUN --mount=type=bind,source=dist,target=/mnt/dist \
    set -eu; \
    cd /mnt/dist; \
    count="$(ls -1 *.tar.gz 2>/dev/null | wc -l)"; \
    [ "${count}" -eq 1 ] || { echo "dist/ must hold exactly one atomic archive; run stage.sh" >&2; exit 1; }; \
    sha256sum -c ./*.tar.gz.sha256; \
    mkdir /tmp/atomic; \
    tar -xzf ./*.tar.gz -C /tmp/atomic --strip-components=1; \
    install -m 0755 /tmp/atomic/atomic /usr/local/bin/atomic; \
    install -d /usr/share/doc/atomic; \
    install -m 0644 /tmp/atomic/LICENSE /tmp/atomic/NOTICE /tmp/atomic/THIRD-PARTY-NOTICES.md /usr/share/doc/atomic/; \
    cd /; \
    probe="$(mktemp -d)"; \
    ATOMIC_HOME="${probe}" atomic --version; \
    rm -rf /tmp/atomic "${probe}"

# State lives in ATOMIC_HOME (a volume), never in the project checkout.
RUN useradd --system --uid 10001 --user-group --home-dir /var/lib/atomic --create-home atomic \
    && install -d -o atomic -g atomic /workspace
ENV ATOMIC_HOME=/var/lib/atomic
USER atomic
WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/atomic"]
CMD ["serve"]
