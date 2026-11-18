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
| [`aws-lambda/`](aws-lambda) | atomic's agents on AWS: the control plane and console on ECS Fargate, atoms that run code on Lambda, Bedrock, RDS for PostgreSQL, telemetry straight to CloudWatch, one CloudFormation template | Design only and forward-looking, assuming atomic 1.0, its own SigV4 telemetry export and the console; lists the gaps in atomic it exposes |
| [`agent-substrate/`](agent-substrate) | atomic on single-node k3s: `atomic serve` on Postgres, atoms that run code in Agent Substrate actors under gVisor, kagent 1.0 conversations through a bridge harness, agentgateway holding every model and tool credential, Istio ambient for the trusted services | Design only and forward-looking, assuming atomic 1.0; records what the Substrate and kagent 1.0 releases change in atomic's plan and the gaps in atomic they expose |

## Shared parts

| Directory | What it is |
| --- | --- |
| [`observability/`](observability) | OpenTelemetry Collector, Prometheus, Tempo, Loki and Grafana configuration, atomic's dashboards, and the telemetry they expect. Every deployment uses it |
| [`docker/`](docker) | Project images: atomic's runtime image plus the toolchains a project's atom commands need, such as uv and Python. Every deployment builds one |

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
