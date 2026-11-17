# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# A sample project image: atomic's runtime image plus one pinned Rust
# toolchain, for a project whose atoms run cargo build, test, clippy or fmt.
#
# Atom commands run in Codex's sandbox with no network by default, so a
# toolchain cannot be fetched during a turn: it has to be in the image. This
# installs one toolchain into /usr/local from Rust's standalone installer,
# without rustup, so cargo and rustc are on the default PATH and need no
# RUSTUP_HOME or CARGO_HOME in the sandbox's environment. Without rustup, a
# project's rust-toolchain.toml is not honoured: set RUST_VERSION to match it.
#
# atomic-runtime is not pulled from a registry: compose.yaml passes the image
# it builds from ../../atomic as this build's atomic-runtime context.
#
# To move to another release, set RUST_VERSION and both checksums. Take each
# checksum from a tarball whose signature you have verified against Rust's
# release key (fingerprint 108F 6620 5EAE B0AA A8DD 5E1C 85AB 96E6 FA1B E5FE):
#
#   f=rust-<version>-x86_64-unknown-linux-gnu.tar.xz   # and aarch64-unknown-linux-gnu
#   curl -fsSLO https://static.rust-lang.org/dist/$f
#   curl -fsSLO https://static.rust-lang.org/dist/$f.asc
#   gpg --verify $f.asc $f && sha256sum $f

ARG RUST_VERSION=1.95.0
ARG RUST_SHA256_AMD64=2e0338f18ecbaa4a0f631b9e80e8b8e26bb6fe77dd5454fba8a70cf96c1e84a1
ARG RUST_SHA256_ARM64=094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e

# Download, check and unpack in a stage of its own, so curl, xz and the
# installer's unused components never reach the project image.
FROM atomic-runtime AS rust
USER root
ARG TARGETARCH
ARG RUST_VERSION
ARG RUST_SHA256_AMD64
ARG RUST_SHA256_ARM64
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
# rustc, cargo and the standard library, with clippy and rustfmt for lint and
# format atoms. Docs, rust-analyzer and the LLVM tools are left out.
RUN set -eu; \
    case "${TARGETARCH}" in \
        amd64) triple=x86_64-unknown-linux-gnu; sum="${RUST_SHA256_AMD64}" ;; \
        arm64) triple=aarch64-unknown-linux-gnu; sum="${RUST_SHA256_ARM64}" ;; \
        *) echo "no Rust checksum for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    curl --proto '=https' --tlsv1.2 -fsSLo rust.tar.xz \
        "https://static.rust-lang.org/dist/rust-${RUST_VERSION}-${triple}.tar.xz"; \
    echo "${sum}  rust.tar.xz" | sha256sum -c -; \
    mkdir rust; \
    tar -xJf rust.tar.xz -C rust --strip-components=1; \
    rust/install.sh --prefix=/usr/local --destdir=/out --disable-ldconfig \
        --components="rustc,cargo,rust-std-${triple},clippy-preview,rustfmt-preview"

FROM atomic-runtime
USER root
# rustc links through the system C compiler, so a test binary needs gcc and
# the C library's development files. Add what the project's crates build
# against here, for example pkg-config and libssl-dev for openssl-sys.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=rust /out/usr/local/ /usr/local/

# Build and test a crate as the user atomic runs as, with a throwaway
# CARGO_HOME, so a toolchain that cannot link fails the build here rather
# than in an atom.
USER atomic
RUN set -eu; \
    probe="$(mktemp -d)"; \
    export CARGO_HOME="${probe}/cargo"; \
    cargo new --quiet --lib --vcs none "${probe}/probe"; \
    cd "${probe}/probe"; \
    cargo test --quiet --offline; \
    cargo clippy --quiet --offline -- -D warnings; \
    cargo fmt --check; \
    rustc --version; \
    cd /; \
    rm -rf "${probe}"
