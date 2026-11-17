# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# A sample project image: atomic's runtime image plus uv, the Python in the
# project's .python-version, and every package in its uv.lock, for a project
# whose atoms run `uv run pytest`, `uv run ruff check` and the like.
#
# Atom commands run in Codex's sandbox with no network by default, so nothing
# can be downloaded during a turn: Python and the locked packages have to be
# in the image. Python goes in /opt/uv/python. The packages go in uv's cache
# at /tmp/uv-cache, because uv needs to write its cache and /tmp is writable
# in Codex's workspace-write sandbox. /etc/uv/uv.toml (uv.toml here) keeps uv
# offline at run time, and a turn's `uv run` builds the worktree's .venv
# from the cache. uv builds the project itself with its built-in uv_build
# backend, which needs no download.
#
# The project's pyproject.toml, uv.lock and .python-version come from the
# `project` build context: the project directory.
# A change to the lock rebuilds the layers after it. A dependency an atom adds
# during a turn is not in the image, so that turn cannot install it.
#
# atomic-runtime is not pulled from a registry: the deployment passes its
# runtime image as this build's atomic-runtime context (see ../README.md).
#
# To move to another uv release, set UV_VERSION and both checksums. Take each
# from a tarball whose build attestation you have verified:
#
#   f=uv-x86_64-unknown-linux-gnu.tar.gz   # and uv-aarch64-unknown-linux-gnu.tar.gz
#   curl -fsSLO https://github.com/astral-sh/uv/releases/download/<version>/$f
#   gh attestation verify $f --repo astral-sh/uv && sha256sum $f
#
# uv checks the Python builds it downloads against checksums built into it,
# so pinning uv pins those as well.

ARG UV_VERSION=0.13.6
ARG UV_SHA256_AMD64=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645
ARG UV_SHA256_ARM64=229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d

# Download and check uv in a stage of its own, so curl never reaches the
# project image.
FROM atomic-runtime AS uv
USER root
ARG TARGETARCH
ARG UV_VERSION
ARG UV_SHA256_AMD64
ARG UV_SHA256_ARM64
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "${TARGETARCH}" in \
        amd64) triple=x86_64-unknown-linux-gnu; sum="${UV_SHA256_AMD64}" ;; \
        arm64) triple=aarch64-unknown-linux-gnu; sum="${UV_SHA256_ARM64}" ;; \
        *) echo "no uv checksum for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    curl --proto '=https' --tlsv1.2 -fsSLo uv.tar.gz \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${triple}.tar.gz"; \
    echo "${sum}  uv.tar.gz" | sha256sum -c -; \
    mkdir /out; \
    tar -xzf uv.tar.gz -C /out --strip-components=1

FROM atomic-runtime
USER root
COPY --from=uv /out/uv /out/uvx /usr/local/bin/
COPY uv.toml /etc/uv/uv.toml
# uv reads its Python directory only from the environment. The install below
# also links python3.<minor> into /usr/local/bin, where uv finds it if a
# sandbox does not pass this variable on.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python

# The settings in uv.toml are offline. The next two steps are the only ones
# that download, so they turn that off for themselves.
COPY --from=project pyproject.toml uv.lock .python-version /opt/uv/lock/

# Python, as root, so atom commands can run it but not change it. Without
# --no-cache, root would create the cache and atomic could not write it.
RUN cd /opt/uv/lock \
    && UV_OFFLINE=false UV_PYTHON_DOWNLOADS=automatic UV_PYTHON_BIN_DIR=/usr/local/bin \
        uv python install --no-cache

# Every locked package, from every group and extra, into the cache. As
# atomic, which has to be able to write the cache. Running `uv sync` fills it
# with exactly what a turn's `uv run` installs.
USER atomic
RUN set -eu; \
    work="$(mktemp -d)"; \
    cp /opt/uv/lock/pyproject.toml /opt/uv/lock/uv.lock /opt/uv/lock/.python-version "${work}/"; \
    cd "${work}"; \
    UV_OFFLINE=false uv sync --frozen --all-groups --all-extras --no-install-project; \
    cd /; \
    rm -rf "${work}"

# Build a fresh environment again with the image's own offline settings, so a
# cache that is incomplete or unusable fails the build here rather than in an
# atom.
RUN set -eu; \
    probe="$(mktemp -d)"; \
    cp /opt/uv/lock/pyproject.toml /opt/uv/lock/uv.lock /opt/uv/lock/.python-version "${probe}/"; \
    cd "${probe}"; \
    uv sync --frozen --all-groups --all-extras --no-install-project; \
    uv run --no-sync python --version; \
    cd /; \
    rm -rf "${probe}"
