# Spikes for the AWS design

Throwaway experiments that check the assumptions in [`../README.md`](../README.md)
against a real AWS account, in a VPC of their own that is deleted afterwards.
They do not need atomic: each probes the platform atomic would run on.

Run on 2026-11-17 in `us-west-2`.

| File | What it is |
| --- | --- |
| [`template.yaml`](template.yaml) | The spike stack: a VPC with a public subnet and an isolated one (no route out), interface endpoints for CloudWatch Logs, metrics, X-Ray, Bedrock Mantle and Bedrock Runtime, six Lambda probes and a Fargate task definition |
| [`run.sh`](run.sh) | Runs every probe and prints the raw results as JSON lines |
| [`landlock_probe.py`](landlock_probe.py) | A follow-up for Fargate, passed to the task as its `PROBE` |

```sh
aws cloudformation deploy --stack-name atomic-spikes \
  --template-file template.yaml --capabilities CAPABILITY_IAM
./run.sh > results.jsonl      # contains the account ID: do not commit it
aws cloudformation delete-stack --stack-name atomic-spikes
```

The traces case of spike 8 and all of spike 9 also need CloudWatch Transaction
Search, which is account-wide. It was turned on for the test and reverted
afterwards: the trace segment destination set back to X-Ray, its Logs resource
policy removed, and the `aws/spans` and `/aws/application-signals/data` log
groups it created deleted.

## 1. Codex's sandbox on Lambda and Fargate

The probe reads the process's security state, tries `unshare(CLONE_NEWUSER)`
and Landlock directly, then runs Codex's own `bwrap` from the `rust-v0.169.0`
release (the binary Codex falls back to when the system has none; its
signature was checked against the release workflow's Sigstore certificate).

| | Lambda | Fargate |
| --- | --- | --- |
| Kernel | 5.10 (Amazon Linux 2) | 6.1 |
| User and capabilities | uid 993, no capabilities | root, Docker's default capabilities |
| Seccomp, no-new-privileges | Filtered (6 filters); `NoNewPrivs` already set | Filtered (1 filter); not set |
| `unshare(CLONE_NEWUSER)` | `EPERM` | `EPERM` |
| Landlock | Not implemented (`ENOSYS`) | **ABI 2** |
| Codex's `bwrap`, any options | Fails at its first step: `prctl(PR_SET_NO_NEW_PRIVS) failed: Operation not permitted` | Fails: `Creating new namespace failed` / `No permissions to create a new namespace` |

**Result.** Codex's sandbox cannot start on either. On Lambda nothing can
sandbox further: no namespaces, no Landlock, and the seccomp policy refuses
even the `prctl` bubblewrap starts with. The microVM is the only boundary.

On Fargate, a process confined its own writes with Landlock and no namespaces
([`landlock_probe.py`](landlock_probe.py)): with writes allowed only under
`/tmp/work`, writing there succeeded, writing to `/tmp` or `/etc` failed with
`EACCES`, and reads still worked. That is not Codex's sandbox (at the pinned
revision Codex rejects its legacy Landlock mode for filesystem-restricted
policies), and ABI 2 has no network control (that needs ABI 4) and no process
isolation. It is an option for atom code atomic runs itself, not for Codex's
shell.

## 2. What a Lambda execution environment keeps

The probe plants a file in `/tmp`, a background process and an environment
variable, then checks for them, crashes the runtime on purpose, and checks
again.

| Invocation | Same process | `/tmp` file | Background process | Environment variable |
| --- | --- | --- | --- | --- |
| Next one, warm | Yes | Present | Running | Present |
| After the runtime exits with an error | No (a new init) | **Present** | Gone | Gone |

**Result.** Everything survives between warm invocations. Exiting the runtime
gives a fresh process, memory and environment, but not a fresh `/tmp`: the
execution environment, and its disk, are reused. A runner must empty `/tmp`
itself; exiting after each execution does not clean it.

## 6. Bedrock from a subnet with no route out

Probes in the isolated subnet called Bedrock's two OpenAI-compatible
endpoints, Mantle (`bedrock-mantle.<region>.api.aws/openai/v1`) and Runtime
(`bedrock-runtime.<region>.amazonaws.com/openai/v1`), with the Responses API
that Codex uses, under three roles: no Bedrock permission, a role scoped to
one model, and a role allowed everything.

| Question | Mantle | Runtime |
| --- | --- | --- |
| Reachable through PrivateLink | Yes, private IP, no NAT | Yes, private IP, no NAT |
| IAM action checked | `bedrock-mantle:CreateInference` | `bedrock:InvokeModel` |
| Resource checked | `arn:aws:bedrock-mantle:<region>:<account>:project/default` | The foundation model or inference profile, and `arn:aws:bedrock:<region>:<account>:project/default` |
| Can IAM limit a role to one model | **No.** With `CreateInference` on the project, every model passed IAM | **Yes.** Allowing one model's ARN and the project, the other model was refused |
| A model list endpoint | No (`/models` returns 404) | No |

Model access was the limit, not the network:

- `openai.gpt-5.6-luna`, Codex's default, answered "not available for this
  account ... contact AWS Sales" on both endpoints, even with every IAM
  permission. OpenAI's frontier models on Bedrock are granted per account.
- `openai.gpt-oss-120b` and `-20b`, which this account can use, are refused on
  Mantle's `/openai/v1` route, the one Codex's Bedrock provider builds, and are
  not found on Runtime. They do work on Mantle's other route, `/v1`: see
  [gpt-oss-120b on Mantle's `/v1` route](#gpt-oss-120b-on-mantles-v1-route).
- `openai.gpt-6-sol` does not exist on Mantle in this Region.

No model call completed from the isolated subnet, so model latency through
PrivateLink is unmeasured.

A follow-up from outside the VPC tried smaller models that need no special
access: `qwen.qwen3-32b-v1:0`, `qwen.qwen3-coder-30b-a3b-v1:0`,
`qwen.qwen3-next-80b-a3b`, `google.gemma-3-12b-it` and `google.gemma-3-4b-it`.

| API | Mantle | Runtime |
| --- | --- | --- |
| `/openai/v1/responses` (what Codex speaks) | Not supported, or the model does not exist there | Not supported, or not found |
| `/openai/v1/chat/completions` | Not supported on this route | **Works**, 230 to 315 ms for a one-word reply |
| Chat completions with a strict JSON schema (`response_format`) | | **Works**: every model returned output valid against an email-triage schema |

Enabling an OpenAI model was then tried, for `openai.gpt-5.6-luna`. Bedrock
reports four things per model (`get-foundation-model-availability`): the
account was authorized and entitled and the model was available in the Region,
but the agreement status was `NOT_AVAILABLE`. Accepting the model's AWS
Marketplace offer (pay per token, no upfront fee) made the agreement
`AVAILABLE` within a minute, yet every call still answered "not available for
this account ... contact AWS Sales". GPT-6 Luna, with no agreement at all,
answers the same, so the gate is on the account, ahead of the agreement and
the quotas. The quotas differ by endpoint as well: GPT-5.6 Luna has 0 tokens
per minute on Runtime's cross-Region and global inference profiles, but 2M
input and 200K output tokens per minute on Mantle. Enabling OpenAI's models
on Bedrock is therefore a request to AWS, not something an account can do for
itself, and a deployment's checklist needs both the access and a non-zero
quota on the endpoint it uses.

So Qwen and Gemma can back lite atoms, through Runtime's chat completions with a
JSON schema, but not Codex atoms. Their triage judgments differed from one
another on the same email, so they suit testing the plumbing, not the
deployment's evals.

## 8. OTLP to CloudWatch from a subnet with no route out

The probe sent OTLP/HTTP JSON, signed with SigV4 by the function's role, to
each CloudWatch endpoint through its interface endpoint (each name resolved to
a private IP), twice.

| Signal | Endpoint | Result | Time, first / second request |
| --- | --- | --- | --- |
| Logs | `logs.<region>.amazonaws.com/v1/logs` | 200 | 50 / 32 ms |
| Metrics, delta sum | `monitoring.<region>.amazonaws.com/v1/metrics` | 200 | 25 / 30 ms |
| Metrics, cumulative sum | the same | 200 | 21 / 21 ms |
| Traces, Transaction Search off | `xray.<region>.amazonaws.com/v1/traces` | **400**: "The OTLP API is supported with CloudWatch Logs as a Trace Segment Destination" | 203 / 22 ms |
| Traces, Transaction Search on | the same | 200 | 245 / 57 ms |

**Result.** Direct export works with no NAT and no collector, and a flush
costs tens of milliseconds per signal once warm. Transaction Search is a
prerequisite for traces; switching it on took about six minutes to become
active. The X-Ray endpoint returns its 400 body as protobuf even though it
labels it `application/json`.

## 9. The console's per-run queries

With the run's spans, logs and metrics from spike 8 ingested:

| Query | Through | Result | Time |
| --- | --- | --- | --- |
| A run's spans by `atomic.workflow.id` | Logs Insights on `aws/spans` | Found, with `attributes.atomic.workflow.id` as a field | 0.9 s |
| A run's logs | Logs Insights on the OTLP log group | Found | 1.4 s |
| A counter | PromQL, `monitoring.<region>.amazonaws.com/api/v1/query`, SigV4 | Found | about 95 ms |

**Result.** The console can draw a run's trace, logs and metrics from
CloudWatch directly. The span index (Transaction Search's indexing share was
0%) is not needed for this: Logs Insights reads every ingested span. What
PromQL returns differs from Prometheus in ways the dashboards have to follow:

- Attribute names keep their dots (`atomic.workflow.id`), resource attributes
  are prefixed `@resource.` (`@resource.service.name`), and series carry
  `__temporality__` and `__monotonicity__` labels.
- Delta counters are stored as such and summed: two delta points of 1 read
  back as 2.
- Every selector needs a metric name; a regular expression on `__name__` is
  refused.

## Not run

Spike 3 (cold starts of real runner images), 4 (a Codex turn streamed through
the gateway), 5 (rollouts) and 7 (agent quality on Bedrock models) need
atomic itself or OpenAI model access on Bedrock.

### gpt-oss-120b on Mantle's `/v1` route

Mantle serves two routes. `/openai/v1` takes OpenAI's own models (the ones this
account cannot use yet); `/v1` takes the rest of Bedrock's catalog, 49 models
in `us-west-2` including `openai.gpt-oss-120b`, and lists them at `/v1/models`.
Signed as `bedrock-mantle` with the caller's role, as before.

| Need | Request on `/v1` | Result |
| --- | --- | --- |
| Codex: Responses API | `POST /v1/responses` | 200 |
| Codex: function tools | A strict `get_weather` tool | Called with valid arguments, 1.6 s |
| Codex: tool results | The call's output sent back with the reasoning item | A correct final answer |
| Codex: streaming | `"stream": true` | Server-sent events with reasoning and text deltas |
| Lite atoms: strict JSON schema, Responses (`text.format`) | Twice | **Hung**: no bytes after 90 and 150 s |
| Lite atoms: strict JSON schema, chat completions (`response_format`) | Twice | Answered, but **not enforced**: Markdown around invalid JSON |
| Lite atoms: a forced tool (`tool_choice` naming the tool) | Three times | Refused: "Only 'auto' tool_choice is supported in response API with Harmony" |
| Lite atoms: a strict `submit` tool, `tool_choice: auto`, one line of instructions | Three times | **Valid against the schema every time**, 0.9 to 3.5 s, the same decision each time |

Codex can be pointed at this route with configuration alone. At the pinned
revision, the built-in `amazon-bedrock` provider accepts overrides of
`base_url`, `auth`, `http_headers` and `aws.{profile,region,credential_export,auth_refresh}`,
and uses a configured `base_url` instead of building one, while still signing
with the AWS SDK's default credential chain:

```toml
model_provider = "amazon-bedrock"
model = "openai.gpt-oss-120b"

[model_providers.amazon-bedrock]
base_url = "https://bedrock-mantle.us-west-2.api.aws/v1"
aws = { region = "us-west-2" }
```

Codex `rust-v0.169.0` was then run against this route in a throwaway
container, under a temporary role allowed only `bedrock-mantle:CreateInference`
(the role, the user that assumed it and their keys were deleted afterwards):

| Turn | Result |
| --- | --- |
| A plain reply | **Works**: "ready" in 3 s |
| A shell tool, with the configuration above | Refused: `Invalid 'tools': unknown variant namespace, expected function or mcp`. Codex sends its sub-agent tools as one `namespace` tool, which this route does not accept. Adding `web_search = "disabled"` and `[features] multi_agent = false` leaves only plain function tools |
| A shell tool, with those two settings | The model calls `exec_command`, then every follow-up request fails ("stream closed before response.completed"): the route rejects an assistant message whose content is a list of parts, the standard Responses shape Codex sends back, and accepts the same text only as a plain string. gpt-oss usually says a sentence before a tool call, so most tool turns break |
| A shell tool, through a loopback shim that rewrites such messages to the plain form | Three requests, all 200, and a final answer; that run happened to have no message to rewrite, so the shim's fix is shown by the replayed requests rather than by this turn |

So Codex on gpt-oss-120b through Mantle's `/v1` route works for plain turns
and needs two settings and a request rewrite for tool turns. It is not the
supported path: AWS's own guide, [OpenAI Codex on Amazon
Bedrock](https://aws.amazon.com/blogs/machine-learning/openai-codex-on-amazon-bedrock/)
(11 November 2026), covers GPT-5.6 Sol, Terra and Luna on Codex's built-in
`amazon-bedrock` provider with no `base_url` override, authenticated through an
IAM Identity Center profile, and does not mention gpt-oss. That is the path
this account's model access gates.

Codex's sandbox did not start in the container either (`bwrap` cannot create
namespaces there), as spike 1 predicts for Fargate and Lambda.

