# Upstream issue drafts

Drafts from the spikes in [`README.md`](README.md), **not filed**. Each is
written for the people who would act on it. Before filing, read it through,
search once more (searches of openai/codex on 2026-11-18 found only the related
issues each draft names), and drop anything you would rather not say in public.

- **Codex** accepts bug reports and root-cause analyses as issues and does not
  accept outside pull requests, so drafts 1 and 2 are issues. atomic carries
  its fix to draft 1 in its fork
  ([atomic-agentic/codex](https://github.com/atomic-agentic/codex), branch
  `atomic`) until a release includes one.
- **Amazon Bedrock** has no public issue tracker: drafts 3 and 4 are for an AWS
  support case. Draft 4 concerns this account's access, so it does not belong
  anywhere public.

Common environment: Codex CLI 0.169.0 (`rust-v0.169.0`, the
`x86_64-unknown-linux-musl` release binary), AWS `us-west-2`, Lambda functions
on x86_64 from the `public.ecr.aws/lambda/python:3.13` base image (kernel
`5.10.255-271-319.1120.amzn2`).

---

## 1. openai/codex: no command can start where `prctl` is refused (AWS Lambda)

**Summary.** In an AWS Lambda function, Codex's model loop runs but every
`exec_command` fails before the command starts, in every sandbox mode,
including `danger-full-access`. Lambda's seccomp policy refuses every `prctl`
call with `EPERM`, and `set_parent_death_signal` treats a failed
`prctl(PR_SET_PDEATHSIG)` as fatal.

**Reproduce.**

1. A Lambda function from `public.ecr.aws/lambda/python:3.13` with the Codex
   release binary, `HOME` and `CODEX_HOME` under `/tmp`, and a model provider
   Codex can reach.
2. In the handler: `codex exec --skip-git-repo-check --sandbox danger-full-access "Use the shell to run 'uname -r', then tell me the value."`

**Expected.** The command runs; Codex's own sandbox cannot start in Lambda
(no user namespaces), which is why `danger-full-access` is used there, with
the function's microVM as the boundary.

**Actual.** Every attempt fails at once:

```text
/usr/bin/bash -lc 'uname -r' in /tmp/work
 exited -1 in 0ms:
Failed to create unified exec process: Operation not permitted (os error 1)
ERROR codex_core::tools::router: error=exec_command failed: CreateProcess { message: "Rejected(\"Failed to create unified exec process: Operation not permitted (os error 1)\")" }
```

The same with `[features] unified_exec_tty = false` and with
`unified_exec = false`.

**Root cause.** `codex-rs/utils/pty/src/process_group.rs`,
`set_parent_death_signal`, runs in `pre_exec` for every command (from
`core/src/spawn.rs`, `utils/pty/src/child_command.rs` and
`spawn_helper_main.rs`) and returns the error when `prctl(PR_SET_PDEATHSIG)`
fails. Probed in the same function, in a child before `exec`:

| Call | In Lambda |
| --- | --- |
| `prctl(PR_SET_PDEATHSIG)`, `prctl(PR_SET_NO_NEW_PRIVS)`, `prctl(PR_SET_DUMPABLE, 0)` | `EPERM` |
| `setsid()`, `setpgid(0, 0)`, `setrlimit(RLIMIT_CORE, 0)` | ok |

**Related.** #40454 is the same function on iSH, where `prctl` returns
`EINVAL` and the `getppid() != parent_pid` guard that follows then kills the
child, because no parent-death signal was armed for it to protect.

**Suggested fix.** Treat a parent-death signal that cannot be armed as best
effort, as `detach_from_tty` already does when `setsid` is refused: when
`prctl` fails because the environment refuses or does not implement it
(`EPERM`, `EACCES`, `ENOSYS`, and `EINVAL` per #40454), start the child
without it, and skip the parent-PID guard, which only closes the race for an
armed signal. The parent still terminates the child's process group when it
is done with it. Two commits on atomic's fork do this, with Linux unit
tests:
[a4b0cac](https://github.com/atomic-agentic/codex/commit/a4b0cac70d89a168616d5cb3b99e5b162e91ce5d)
tolerates `EPERM`, `EACCES` and `ENOSYS`, and
[f8e52f3](https://github.com/atomic-agentic/codex/commit/f8e52f30a7d56b045b93068c156aae8a4a312ba6)
adds `EINVAL` and skips the guard when no signal was armed. With the first,
commands run in Lambda: `uname -r` and `head -1 /proc/meminfo` succeeded and
the turn completed in 2 to 3 s warm.

**Why it matters.** Lambda is a natural place to run one agent turn per
invocation with a microVM per execution environment. Today Codex can think
there but not act.

---

## 2. openai/codex: a TTY command fails where the host has no pseudo-terminals

**Summary.** Lambda has no `/dev/ptmx` or `/dev/pts`. When the model asks for
a TTY, `exec_command` fails instead of running the command over pipes.

**Actual.**

```text
Failed to create unified exec process: failed to openpty: Os { code: 2, kind: NotFound, message: "No such file or directory" }
```

**Workaround.** `[features] unified_exec_tty = false`: requests for a TTY are
then served over pipes (`core/src/tools/handlers/unified_exec/preparation.rs`),
and commands run (with the fix for draft 1).

**Suggested fix.** When `openpty` fails because the host has no
pseudo-terminal devices (`ENOENT`, `ENODEV`, `ENXIO`), fall back to pipes for
that command and say so in its output, or detect it once at startup and
disable TTY execution with a warning, as Codex already warns when bubblewrap
is missing.

---

## 3. Amazon Bedrock (support case): Mantle's `/v1` Responses route rejects valid requests

**Summary.** `POST https://bedrock-mantle.us-west-2.api.aws/v1/responses` with
`openai.gpt-oss-120b`, signed with SigV4 (`bedrock-mantle`), rejects or hangs
on three request shapes that are valid in the Responses API. The first two are
what Codex sends on every multi-step turn, so Codex works on this route only
with a proxy rewriting its requests. Dates 2026-11-17 and 18.

1. **An assistant message item with list content.**

   ```json
   {"type": "message", "role": "assistant", "id": "msg_…", "status": "completed",
    "content": [{"type": "output_text", "text": "Running the kernel version command.", "annotations": []}]}
   ```

   in `input`, after a tool call, gives HTTP 200 with an error event:
   `invalid_prompt: SubmitRequestFailure: code=-32602, msg=219 validation
   errors for ResponsesRequest` (the same with `stream: false`, without `id`
   and `status`, and with only the required fields). The same message as
   `{"role": "assistant", "content": "Running the kernel version command."}`
   is accepted. Every other item kind in the same input (developer and user
   messages, a reasoning item, the function call and its output) is accepted.
2. **A tool of type `namespace`.** `Invalid 'tools': unknown variant
   namespace, expected function or mcp` (HTTP 400).
3. **Strict structured output.** `text.format` with
   `{"type": "json_schema", "strict": true, …}` returned no bytes within 90 s
   or 150 s, twice. On `/v1/chat/completions` the same schema in
   `response_format` is answered in about 3 s but not enforced: Markdown around
   invalid JSON.

**Expected.** The same behaviour as the Responses API elsewhere: list content
for an assistant message, namespace tools (or a documented list of supported
tool types), and strict JSON-schema output.

---

## 4. Amazon Bedrock (support case, this account only): OpenAI models stay unavailable after the agreement

**Not for anywhere public.** In `us-west-2`, `get-foundation-model-availability`
for `openai.gpt-5.6-luna` reports `authorizationStatus: AUTHORIZED`,
`entitlementAvailability: AVAILABLE` and `regionAvailability: AVAILABLE`. After
accepting the model's Marketplace offer, `agreementAvailability` became
`AVAILABLE` within a minute, yet every request, on Mantle (`/openai/v1`) and on
Runtime (`global.` and in-Region profiles), still answers "openai.gpt-5.6-luna
is not available for this account … contact AWS Sales". `openai.gpt-6-luna`,
without an agreement, answers the same. Service Quotas show 0 tokens per
minute for GPT-5.6 Luna on Runtime's cross-Region and global profiles, and
non-zero values on Mantle.

**Request.** Enable OpenAI's models on Bedrock for the account (GPT-5.6 Luna
first, for Codex as in AWS's "OpenAI Codex on Amazon Bedrock" guide), and say
what else gates them; then a non-zero Runtime quota for GPT-5.6 Luna.
