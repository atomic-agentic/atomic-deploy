# atomic-deploy

Ways to deploy [atomic](https://github.com/atomic-agentic/atomic), a Rust
framework for small, typed, single-purpose agents with durable workflows and
human sign-off at gates.

atomic is in early development, and so is this repository. Each deployment
here is built alongside atomic's runtime, so problems with running atomic in
real environments show up while its design can still change. Where a
deployment depends on something atomic does not do yet, its README says so.

## Deployments

| Directory | What it runs | Status |
| --- | --- | --- |
| [`docker-compose/`](docker-compose) | atomic on one host, on Postgres, with the observability stack | Stack and dashboards checked with sample telemetry; the atomic image runs a Codex turn. atomic's `serve` and Postgres storage are pending |

## Shared parts

| Directory | What it is |
| --- | --- |
| [`observability/`](observability) | OpenTelemetry Collector, Prometheus, Tempo, Loki and Grafana configuration, atomic's dashboards, and the telemetry they expect. Every deployment uses it |

## Conventions

- **Images are pinned by digest**, with the tag beside it for people.
- **atomic's image is built from a release archive**, the artifact releases
  sign and describe in their SBOM. The build checks it against its
  `.sha256`; verify a downloaded release's signature before staging it.
- **Configuration comes from the environment.** Shared files take endpoints
  and credentials from variables, so another deployment can mount them
  unchanged.
- **Secrets are never committed.** Each deployment has an `.env.example`;
  the `.env` beside it is git-ignored.
- **Nothing is exposed by default.** Published ports bind to 127.0.0.1.

## License

Apache-2.0, the same as atomic. See [LICENSE](LICENSE).
