#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Put one atomic release archive, with its checksum, in dist/ for the image
# build.
#
#   ./stage.sh                         from a sibling atomic checkout (../../../atomic)
#   ./stage.sh --checkout <path>       from the atomic checkout at <path>
#   ./stage.sh --archive <file>        from an archive already on disk
#
# A checkout must have a packaged release in target/dist/, which atomic's
# scripts/release-package.sh writes after a release build:
#
#   scripts/dev.sh cargo build --release --locked -p atomic-agentic --target x86_64-unknown-linux-gnu
#   scripts/dev.sh scripts/release-package.sh x86_64-unknown-linux-gnu
#
# The archive's .sha256 file must sit next to it; the image build checks it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="${HERE}/dist"

case "$(uname -m)" in
    x86_64 | amd64) TRIPLE="x86_64-unknown-linux-gnu" ;;
    aarch64 | arm64) TRIPLE="aarch64-unknown-linux-gnu" ;;
    *) echo "stage.sh: unsupported machine $(uname -m)" >&2; exit 1 ;;
esac

checkout="${HERE}/../../../atomic"
archive=""
while [ $# -gt 0 ]; do
    case "$1" in
        --checkout) checkout="${2:?--checkout needs a path}"; shift 2 ;;
        --archive) archive="${2:?--archive needs a file}"; shift 2 ;;
        -h | --help) sed -n '3,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "stage.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done

if [ -z "${archive}" ]; then
    [ -d "${checkout}" ] || { echo "stage.sh: no atomic checkout at ${checkout}" >&2; exit 1; }
    shopt -s nullglob
    found=("${checkout}"/target/dist/atomic-*-"${TRIPLE}".tar.gz)
    shopt -u nullglob
    if [ "${#found[@]}" -ne 1 ]; then
        echo "stage.sh: expected one atomic-*-${TRIPLE}.tar.gz in ${checkout}/target/dist, found ${#found[@]}" >&2
        exit 1
    fi
    archive="${found[0]}"
fi

[ -f "${archive}" ] || { echo "stage.sh: ${archive} does not exist" >&2; exit 1; }
[ -f "${archive}.sha256" ] || { echo "stage.sh: ${archive}.sha256 is missing" >&2; exit 1; }
(cd "$(dirname "${archive}")" && sha256sum -c "$(basename "${archive}").sha256" >/dev/null) \
    || { echo "stage.sh: ${archive} does not match its checksum" >&2; exit 1; }

rm -rf "${DIST}"
mkdir -p "${DIST}"
cp "${archive}" "${archive}.sha256" "${DIST}/"
echo "staged $(basename "${archive}")"
echo "next: docker compose up -d --build (the atomic image is rebuilt from it)"
