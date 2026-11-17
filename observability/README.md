# Observability for atomic

An OpenTelemetry Collector, a Grafana stack (Prometheus, Tempo and Loki), and
atomic's dashboards. Nothing here is tied to one way of running atomic: each
deployment in this repository mounts these files and sets a few environment
variables. [`../docker-compose`](../docker-compose) is the first.

```text
atomic ──OTLP──▶ collector ──▶ Prometheus  metrics, through its OTLP receiver
                     │     ──▶ Tempo       traces
                     │     ──▶ Loki        logs
                     └─ span metrics, tail sampling, content backstop

Grafana ◀── Prometheus, Tempo, Loki, and atomic's Postgres (read-only)
```

| Path | What it is |
| --- | --- |
| [`otel-collector/collector.yaml`](otel-collector/collector.yaml) | The collector pipeline. Endpoints come from `PROMETHEUS_OTLP_ENDPOINT`, `TEMPO_OTLP_ENDPOINT` and `LOKI_OTLP_ENDPOINT` |
| [`prometheus/prometheus.yaml`](prometheus/prometheus.yaml) | OTLP translation and promoted resource attributes. Start Prometheus with `--web.enable-otlp-receiver` |
| [`tempo/tempo.yaml`](tempo/tempo.yaml) | Single-process Tempo on local disk |
| [`loki/loki.yaml`](loki/loki.yaml) | Single-process Loki on local disk |
| [`grafana/provisioning`](grafana/provisioning) | Datasources with fixed UIDs, and the dashboard folder |
| [`grafana/dashboards`](grafana/dashboards) | Generated dashboards. Do not edit by hand |
| [`metrics.md`](metrics.md) | The telemetry the dashboards expect from atomic |
| [`tools/`](tools) | The dashboard generator, a query checker and a sample-telemetry sender |

## The dashboards

| Dashboard | Shows | Reads |
| --- | --- | --- |
| **Overview** | Runs, active runs, open gates, spend, tokens, step and gate p95, repairs, recoveries, recent warnings | Prometheus, Loki |
| **Workflows** | Runs by outcome, active runs, step duration by step, queue wait, attempts, checkpoints, span-derived rates, failed and recovered step attempts | Prometheus, Tempo |
| **Models and cost** | Tokens, spend by provider, workflow and project, call rate and latency, throttling, fallbacks | Prometheus |
| **People and gates** | Open gates, gate wait, decisions by outcome and channel, in-turn approval wait, steering | Prometheus |
| **Quality and tools** | Schema repairs, validation failures, repairs per attempt, eval pass rate, tool calls, errors and latency | Prometheus |
| **Runtime** | Instances, runner utilization, recoveries, memory, the work graph, Postgres | Prometheus, Loki |
| **Durable runs** | Unfinished runs, runs by status and executor, run and step durations, recent failures, queues | atomic's Postgres |
| **Telemetry pipeline** | What the collector received, refused, sent and failed to send; sampling; backends up | Prometheus |

Every dashboard built on telemetry has an **Environment** filter
(`deployment.environment.name`), applied to its metrics, logs and traces.
**Durable runs** reads one database, and **Telemetry pipeline** describes the
stack itself, so neither has one. Each dashboard links to the others.
Duration charts carry exemplars: a point links to the trace of the step
attempt behind it.

Panels stay empty until atomic exports the metric they read. **Durable runs**
works from the database alone, and the span-derived panels on **Workflows**
work as soon as atomic exports traces.

## Changing a dashboard

The JSON is generated. Edit [`tools/build-dashboards.py`](tools/build-dashboards.py), then:

```sh
python3 -I observability/tools/build-dashboards.py          # writes grafana/dashboards/*.json
python3 -I observability/tools/build-dashboards.py --check  # fails if the JSON is stale
```

Grafana reloads provisioned dashboards within 30 seconds. Changes made in the
Grafana UI cannot be saved over a provisioned dashboard; save a copy to
experiment, then move the change into the generator.

## Checking a deployment

With the stack up, send synthetic telemetry and run every dashboard query
through Grafana:

```sh
python3 -I observability/tools/send-sample-telemetry.py --endpoint http://127.0.0.1:4318 --minutes 3
python3 -I observability/tools/check-dashboards.py --url http://127.0.0.1:3000 --password "$GRAFANA_ADMIN_PASSWORD"
```

The sample data is labelled `deployment.environment.name=sample` and is
invented: no workflow ran. The checker reports each query as ok, empty or an
error, and exits non-zero on any error (`--strict` also fails on empty).

## Using it elsewhere

Each component reads its endpoints from the environment, so another
deployment mounts the same files and sets:

| Component | Variables |
| --- | --- |
| Collector | `PROMETHEUS_OTLP_ENDPOINT`, `TEMPO_OTLP_ENDPOINT`, `LOKI_OTLP_ENDPOINT`, `TRACE_SAMPLE_PERCENT`, `TRACE_DECISION_WAIT` |
| Grafana | `PROMETHEUS_URL`, `PROMETHEUS_SAMPLE_INTERVAL`, `TEMPO_URL`, `LOKI_URL`, `ATOMIC_PG_HOST`, `ATOMIC_PG_DATABASE`, `ATOMIC_PG_USER`, `ATOMIC_PG_PASSWORD`, `ATOMIC_PG_SSLMODE` |
| Tempo, Loki | `TEMPO_RETENTION`, `LOKI_RETENTION`, `LOKI_DISK_FULL_THRESHOLD` |
| atomic | `OTEL_METRIC_EXPORT_INTERVAL` no longer than `PROMETHEUS_SAMPLE_INTERVAL` (15 seconds in Compose) |

Start Tempo and Loki with `-config.expand-env=true`, which the `${VAR:-default}`
values in their configs need, and Prometheus with `--web.enable-otlp-receiver`
and `--enable-feature=exemplar-storage`; without the last, exemplars are
dropped and duration charts no longer link to traces.

A deployment can merge its own settings over the collector's with a second
`--config` (see [`../docker-compose/collector/postgres.yaml`](../docker-compose/collector/postgres.yaml)).
On Kubernetes, the files become ConfigMaps, and these change:

- **Prometheus scrape targets** in `prometheus.yaml` name the Compose
  services; replace them with service discovery or the operator's monitors.
  Under the Prometheus Operator the file is not mounted at all: move its
  `otlp:` block (promoted attributes and translation) into the `Prometheus`
  resource, and the feature flags into its `enableFeatures`.
- **Tail sampling and span metrics need a whole trace in one collector.**
  With more than one collector replica, put a load-balancing tier in front
  that routes by trace ID. Without it, sampling sees partial traces, and
  every replica emits the same span-metric series, which corrupts rates.

Tempo and Loki here are single-process on local disk. For more than one
replica, use object storage and their scalable modes.

## Image versions

| Component | Image |
| --- | --- |
| Collector | `otel/opentelemetry-collector-contrib:0.164.0` |
| Prometheus | `prom/prometheus:v3.5.5` (the 3.5 long-term-support line) |
| Tempo | `grafana/tempo:3.1.1` |
| Loki | `grafana/loki:3.7.9` |
| Grafana | `grafana/grafana:12.4.15` |

Each deployment pins them by digest. Tempo 3 and Loki 3 configs differ from
their 2.x versions; check `-config.verify` (Tempo) and `-verify-config`
(Loki) after any upgrade.
