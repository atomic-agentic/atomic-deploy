# atomic's telemetry, as the dashboards read it

The dashboards in this directory assume the instruments, attributes and span
shapes below. **This is a proposal ahead of the runtime:** atomic does not yet
export most of these. When atomic's exported names settle, change
[`tools/build-dashboards.py`](tools/build-dashboards.py) and this file
together, regenerate the dashboards, and run the checks in
[README.md](README.md#checking-a-deployment).

## Resource

Every signal from atomic carries:

| Attribute | Example | Used for |
| --- | --- | --- |
| `service.name` | `atomic` | All atomic queries (`service_name="atomic"`) |
| `service.namespace` | `atomic` | Prometheus job (`atomic/atomic`) |
| `service.version` | `0.1.0` | Comparing releases |
| `service.instance.id` | `compose-atomic-0` | One runtime process. Compose sets it to the executor ID |
| `deployment.environment.name` | `compose`, `staging` | The **Environment** variable on every dashboard |

## Rules the dashboards rely on

- **No run, workflow-run, step-attempt or trace ID is ever a metric
  attribute.** They are unbounded. Histograms carry exemplars instead, which
  link a point on a chart to the trace of one step attempt.
- **One trace per step attempt**, linked to the workflow's start. A gate that
  waits for days never holds a span open.
- **Replay adds nothing.** A step served from its checkpoint after a crash
  emits no spans, no attempt and no usage. Spans of a recovered run carry
  `atomic.replay = true`.
- **Content is off by default.** Prompts, completions and tool arguments are
  not telemetry unless a project turns capture on. The collector drops the
  standard GenAI content attributes, and the `prompt`, `arguments` and
  `output` attributes of Codex's `codex.*` events, from spans, span events
  and log records as a backstop. It does not inspect log bodies.
- **Metrics are exported at least every 15 seconds**
  (`OTEL_METRIC_EXPORT_INTERVAL=15000`). The dashboards' rate windows are
  derived from that; at the SDK default of 60 seconds they come out empty.
- **Counters start at zero.** atomic should export each counter at 0 for the
  series it knows when it starts. Prometheus computes `increase()` from two
  samples, so a series whose first sample is already 3 (three runs recovered
  at start-up) adds nothing to the dashboards.

## Metrics

OTLP names follow OpenTelemetry conventions. Prometheus names are what
Prometheus's OTLP receiver makes of them (`UnderscoreEscapingWithSuffixes`):
dots become underscores, the unit is appended (`s` → `_seconds`, `By` →
`_bytes`, `1` → `_ratio` on gauges), monotonic sums get `_total`, and
attribute names change the same way (`atomic.workflow.name` →
`atomic_workflow_name`).

### Workflows and steps

| Instrument | Type | Unit | Attributes | Prometheus |
| --- | --- | --- | --- | --- |
| `atomic.workflow.runs` | counter | `{run}` | `atomic.workflow.name`, `atomic.project`, `atomic.run.status` = `started` \| `completed` \| `failed` \| `parked` \| `rejected` | `atomic_workflow_runs_total` |
| `atomic.workflow.active` | gauge | `{run}` | `atomic.workflow.name`, `atomic.run.state` = `running` \| `waiting` | `atomic_workflow_active` |
| `atomic.step.duration` | histogram | `s` | `atomic.workflow.name`, `atomic.step`, `atomic.atom.kind` | `atomic_step_duration_seconds` |
| `atomic.step.queue.duration` | histogram | `s` | `atomic.workflow.name` | `atomic_step_queue_duration_seconds` |
| `atomic.step.attempts` | counter | `{attempt}` | `atomic.atom`, `atomic.attempt.outcome` | `atomic_step_attempts_total` |
| `atomic.checkpoint.writes` | counter | `{checkpoint}` | `atomic.workflow.name` | `atomic_checkpoint_writes_total` |
| `atomic.runtime.recoveries` | counter | `{run}` | `atomic.workflow.name` | `atomic_runtime_recoveries_total` |

Step duration is the attempt's active time; gate waits are measured
separately.

### People

| Instrument | Type | Unit | Attributes | Prometheus |
| --- | --- | --- | --- | --- |
| `atomic.gate.open` | gauge | `{gate}` | `atomic.workflow.name` | `atomic_gate_open` |
| `atomic.gate.wait.duration` | histogram | `s` | `atomic.workflow.name`, `atomic.gate.decision` = `approved` \| `rejected` \| `expired` | `atomic_gate_wait_duration_seconds` |
| `atomic.gate.decisions` | counter | `{decision}` | `atomic.gate.decision`, `atomic.gate.channel` | `atomic_gate_decisions_total` |
| `atomic.approval.wait.duration` | histogram | `s` | `atomic.approval.decision` | `atomic_approval_wait_duration_seconds` |
| `atomic.steering.events` | counter | `{event}` | `atomic.steering.kind` = `steer` \| `stop` | `atomic_steering_events_total` |

Gate waits are computed from the durable open and decision times, so a
restart does not reset them.

### Quality and tools

| Instrument | Type | Unit | Attributes | Prometheus |
| --- | --- | --- | --- | --- |
| `atomic.schema.repairs` | counter | `{repair}` | `atomic.atom` | `atomic_schema_repairs_total` |
| `atomic.validation.failures` | counter | `{failure}` | `atomic.atom` | `atomic_validation_failures_total` |
| `atomic.eval.pass_rate` | gauge | `1` | `atomic.atom` | `atomic_eval_pass_rate_ratio` |
| `atomic.tool.duration` | histogram | `s` | `gen_ai.tool.name` | `atomic_tool_duration_seconds` |
| `atomic.tool.errors` | counter | `{error}` | `gen_ai.tool.name`, `error.type` | `atomic_tool_errors_total` |

### Models and cost

| Instrument | Type | Unit | Attributes | Prometheus |
| --- | --- | --- | --- | --- |
| `gen_ai.client.token.usage` | histogram | `{token}` | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.token.type` | `gen_ai_client_token_usage` |
| `gen_ai.client.operation.duration` | histogram | `s` | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` | `gen_ai_client_operation_duration_seconds` |
| `atomic.cost` | counter | `USD` | `atomic.model.tier`, `gen_ai.provider.name`, `atomic.project`, `atomic.workflow.name` | `atomic_cost_USD_total` |
| `atomic.model.throttled` | counter | `{request}` | `gen_ai.provider.name`, `atomic.model.tier` | `atomic_model_throttled_total` |
| `atomic.model.fallbacks` | counter | `{request}` | `atomic.model.tier` | `atomic_model_fallbacks_total` |
| `codex.turns` | counter | `{turn}` | `atomic.model.tier` | `codex_turns_total` |

The two `gen_ai.client.*` instruments are the OpenTelemetry GenAI semantic
conventions. Cost counts priced calls only; a model with no price adds
nothing rather than zero-cost noise.

### Work graph, runners and process

| Instrument | Type | Unit | Attributes | Prometheus |
| --- | --- | --- | --- | --- |
| `atomic.graph.items` | gauge | `{item}` | `atomic.graph.state` = `ready` \| `claimed` \| `blocked` \| `closed` | `atomic_graph_items` |
| `atomic.graph.claim.contention` | counter | `{claim}` | `atomic.project` | `atomic_graph_claim_contention_total` |
| `atomic.graph.message.cursor.lag` | gauge | `{message}` | `atomic.project` | `atomic_graph_message_cursor_lag` |
| `atomic.runner.utilization` | gauge | `1` | `atomic.runner.backend` | `atomic_runner_utilization_ratio` |
| `process.memory.usage` | gauge | `By` | `process.memory.type` | `process_memory_usage_bytes` |
| `process.uptime` | gauge | `s` | | `process_uptime_seconds` |

### Computed in the collector

The collector's `spanmetrics` connector derives call counts and durations
from every span before sampling:

| Prometheus | Labels |
| --- | --- |
| `traces_span_metrics_calls_total` | `service_name`, `span_name`, `span_kind`, `status_code`, plus `atomic_workflow_name`, `atomic_step`, `gen_ai_operation_name`, `gen_ai_agent_name`, `gen_ai_request_model`, `gen_ai_tool_name` |
| `traces_span_metrics_duration_seconds` | the same |

These work as soon as atomic exports spans, before it has its own metrics.

## Spans

| Span | Name | Key attributes |
| --- | --- | --- |
| Step attempt (trace root) | `step <step>` | `gen_ai.operation.name = invoke_agent`, `gen_ai.agent.name`, `atomic.workflow.name`, `atomic.workflow.id`, `atomic.step`, `atomic.attempt`, `atomic.replay`, a link to the workflow's start |
| Model call | `chat <model>` | `gen_ai.operation.name = chat`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `atomic.cost_usd` |
| Tool call | `execute_tool <tool>` | `gen_ai.operation.name = execute_tool`, `gen_ai.tool.name` |
| Gate | recorded when the gate closes | `atomic.gate.decision`, from durable timestamps |

`atomic.workflow.id` is the run ID. It belongs on spans and logs, where it is
searchable, and never on metrics.

With `TRACE_SAMPLE_PERCENT` below 100, the collector keeps a trace if any of
its spans has an error, lasts over 30 seconds, has a rejected or expired gate
(`atomic.gate.decision`), a schema repair (`atomic.schema.repairs >= 1`) or
`atomic.replay = true`, and samples the rest. It judges a trace
`TRACE_DECISION_WAIT` (60 seconds) after its first span arrives. A step's
root span, which carries the step's own outcome, is exported only when the
step ends, so for a longer step the decision rests on its child spans alone:
a step that fails after three minutes without a failed child can be sampled
away. Steps longer than the wait need atomic to put the deciding attributes
on a span it exports early, or the wait raised to match.

## Logs

OTLP log records with the resource above, linked to their span by trace and
span ID. Loki indexes only `service.name`, `service.namespace` and
`deployment.environment.name`; everything else, run IDs included, is
structured metadata. The tamper-evident audit record is a separate stream
and is not sampled; it should go to its own store, not only to Loki.

## Durable records

The **Durable runs** dashboard reads durare's tables in Postgres directly
(`workflow_status` and `operation_outputs`, in the `dbos` schema by default;
statuses `PENDING`, `ENQUEUED`, `DELAYED`, `SUCCESS`, `ERROR`, `CANCELLED`
and `MAX_RECOVERY_ATTEMPTS_EXCEEDED`).
It selects statuses, names, executor IDs and timestamps only, never inputs,
outputs or error text. It needs no telemetry export, and it keeps working
when the collector is down.
