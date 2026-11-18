# SPDX-License-Identifier: Apache-2.0
#
# Spike 6's bridge harness, built inside kagent's Go module at v1.0.1 so it
# uses the SDK the installed controller expects. Fetches Go modules at build.
#
#   docker build --build-context kagent=<kagent v1.0.1 checkout>/go \
#     -t localhost:5000/atomic-kagent-bridge-spike:<tag> .

# golang:1.27.4-trixie (kagent's go.mod asks for 1.27.3 or later)
FROM golang@sha256:f18c3d73a184fb0582828510722e723b63a93f7188d11dd223d9be6f6880e65d AS build
WORKDIR /src
COPY --from=kagent . .
COPY main.go cmd/atomic-bridge-spike/main.go
RUN CGO_ENABLED=0 go build -trimpath -o /out/bridge ./cmd/atomic-bridge-spike

# gcr.io/distroless/static-debian13:latest. Root, to listen on port 80.
FROM gcr.io/distroless/static-debian13@sha256:1a64f46364620465ef9e9a8f5413757dfdf22695e050bbfd7303eeaf17a59383
COPY --from=build /out/bridge /bridge
ENTRYPOINT ["/bridge"]
