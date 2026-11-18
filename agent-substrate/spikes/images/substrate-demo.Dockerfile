# SPDX-License-Identifier: Apache-2.0
#
# Builds one of Agent Substrate's demo servers from a checkout of
# agent-substrate/substrate, with its vendored modules and no network fetches.
# The binary lands where Substrate's own ko-built images put it, so the demo's
# ActorTemplate runs unchanged.
#
#   docker build -f substrate-demo.Dockerfile --build-arg DEMO=counter \
#     -t localhost:5000/substrate-demo-counter:<substrate commit> <substrate checkout>

# golang:1.27.4-trixie, Substrate's go.mod toolchain.
FROM golang@sha256:f18c3d73a184fb0582828510722e723b63a93f7188d11dd223d9be6f6880e65d AS build
ARG DEMO
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 GOFLAGS=-mod=vendor go build -trimpath -o /out/app ./demos/${DEMO}

# gcr.io/distroless/static-debian13:latest. Root, because the counter demo
# writes to the root filesystem.
FROM gcr.io/distroless/static-debian13@sha256:1a64f46364620465ef9e9a8f5413757dfdf22695e050bbfd7303eeaf17a59383
ARG DEMO
COPY --from=build /out/app /ko-app/${DEMO}
