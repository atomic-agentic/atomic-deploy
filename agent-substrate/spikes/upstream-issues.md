# Upstream issue drafts

Drafts from the spikes in [`results.md`](results.md), **not filed**. Each is
written for the target project's maintainers. Before filing, read it through,
search the tracker once more (searches on 2026-11-18 with the terms in each
draft found nothing), and drop anything you would rather not say in public.

Common environment for all of them: single-node k3s v1.37.2+k3s1 (containerd
2.3.7), Rocky Linux 10.2, kernel 6.12, SELinux enforcing; kagent v1.0.1 and
Agent Substrate v1.0.0 installed with `kagent install --profile minimal`
(kagent-dev/substrate charts, agentgateway data plane, gVisor workers);
Istio 1.32.0 ambient; agentgateway v1.7.0.

---

## 1. agent-substrate/substrate: a golden build that cannot be placed leaves the ActorTemplate's status empty

**Summary.** When no worker has room for an ActorTemplate's golden actor, the
golden build fails and is retried, but nothing of that reaches the API: the
template shows no golden tag, no error, and an empty `status`. The only trace
is in `ate-api-server`'s log.

**Reproduce.**

1. A WorkerPool of two workers with 1000 millicores each; resume two actors
   whose templates ask for 1000 millicores, so both workers are full.
2. Create another template on that pool (any size).
3. `kubectl ate get actor-template <name> -a <atespace> -o json`.

**Expected.** The template's status says why the golden snapshot is not ready
(for example a condition with the `AssignWorker` error), as the router does
for a request (`503 no worker has room for the actor`).

**Actual.** `GOLDEN TAG` and `ERROR` are empty and `status` is `{}` for as
long as the workers are full. `ate-api-server` logs, about every 9 s:

```text
Failed to reconcile actor template, requeueing ... "err":"while resuming golden actor:
workflow failed at step AssignWorker: no worker has room for the actor"
```

Once the workers are freed, the golden snapshot is ready within 10 s.

**Why it matters.** A system creating templates through the API (ours
reconciles them from its own definitions) cannot tell "still building" from
"cannot be placed", so it can only time out blind.

---

## 2. agent-substrate/substrate: nothing produces the worker-proposed suspend

**Summary.** `WorkerService.RequestActorSuspend` and atelet's forwarding are
in place ("Only the Worker can observe what makes an Actor worth reclaiming"),
but at `3d574499` nothing calls `internal/ateworker/suspend`: the package has
no importers. An idle actor therefore stays `RUNNING`, holding its worker's
capacity, until a client suspends it, and the scheduler evicts nothing to make
room.

**Observed.** With both workers held by idle running actors, a request to a
suspended actor through the router parked for the budget (60 s in kagent's
charts) and failed with `503 no worker has room for the actor`; neither idle
actor was touched.

**Question / request.** Is an idleness detector planned for the workers (for
example no open connections and no CPU for a template-defined period), or an
`ActorTemplate` idle-suspend policy? Until then, could the docs say plainly
that suspend is entirely the caller's job, and that a crashed caller leaves
its actors running?

---

## 3. agent-substrate/substrate: the egress gateway's upstream connection pool cannot be configured

**Summary.** On the agentgateway data plane, `atenet-egress` keeps upstream
connections on agentgateway's defaults (`pool_idle_timeout` 90 s, no
`pool_max_size`); its generated configuration sets no pool options, and there
is no install setting for them. The router, by contrast, sets
`poolMaxSize: 0`.

**Why it matters.** Admission decisions taken by anything between the egress
gateway and the destination apply per connection. With Istio ambient, a
plaintext connection the egress gateway opened while a destination namespace
was PERMISSIVE kept working for as long as it was reused at least every 90 s
after the namespace turned STRICT (104 s of requests every 15 s, all 200;
refused only after a 100 s pause). Restarting `atenet-egress` was the only way
to apply the new policy at once.

**Request.** An install option for the egress gateway's upstream pool (idle
timeout and maximum size, as the router already uses), or a documented way to
set it.

---

## 4. kagent-dev/substrate: the router's `requestTimeout: "60s"` bounds only the resume call

**Summary.** The charts render the agentgateway router configuration with

```yaml
substrateIngress:
  host: api.ate-system.svc:443
  requestParking: {budget: "60s"}
  policies:
    http:
      requestTimeout: "60s"
```

In agentgateway v1.7.0 those `policies` apply to the "Backend that receives
ResumeActor calls" (`crates/agentgateway/src/http/substrate/ingress.rs`), so
the 60 s bounds the router's `ResumeActor` call, not requests to actors. The
actor routes set no timeout and agentgateway applies none: requests that
answered after 90 s and 330 s, and a 90 s streamed response, all completed
through both the HTTPS route and the `CONNECT` path. Upstream's Envoy data
plane has a 5-minute default route timeout (`--route-timeout`), so the two
data planes behave differently.

**Request.** Either a route-level timeout for actor traffic matching the Envoy
data plane (and a chart value for it), or a comment in the rendered
configuration saying what `requestTimeout` there does.

**Related observation.** A router restart ends every request in flight about
6 s after it starts draining (`drain duration expired with pending
connections, forcefully shutting down`); a chunked response cut that way
raises `IncompleteRead` in a strict client but ends silently in one that reads
line by line. Worth a line in the docs for anyone streaming model output
through the router.

---

## 5. istio/istio: ztunnel keeps a plaintext connection after PeerAuthentication turns STRICT

**Summary.** In ambient mode, ztunnel closes established HBONE connections
when an AuthorizationPolicy change denies them (`connection closed due to
policy change`), but an inbound plaintext connection from outside the mesh,
admitted while the namespace was PERMISSIVE, stays open and keeps carrying
requests after a `PeerAuthentication` in that namespace switches to STRICT.

**Environment.** Istio 1.32.0, ambient profile (`istio-cni` with
`global.platform=k3s`), k3s v1.37.2 with flannel.

**Reproduce.**

1. Namespace `atomic` enrolled in ambient, `PeerAuthentication` PERMISSIVE,
   an HTTP server pod in it.
2. A client outside the mesh that reuses one keep-alive connection (here an
   HTTP proxy with a 90 s idle pool), sending a request every 15 s.
3. Switch the `PeerAuthentication` to STRICT.

**Expected.** The plaintext connection is closed, as HBONE connections are on
an AuthorizationPolicy change, or the documentation says STRICT applies only
to new connections.

**Actual.** Every request on the existing connection keeps succeeding (104 s
of requests in our run) until the client lets it go idle; ztunnel logs the
connection only when it ends, 265 s after it opened. New plaintext
connections are refused (`connection reset`). ztunnel had processed the
change (`handling RBAC update istio_converted_static_strict`).

---

## 6. kagent-dev/kagent: in `insecure` auth mode a caller's `X-User-Id` reaches the harness as authenticated

**Summary.** With the default `config.server.auth.mode: insecure`, a request
to the A2A gateway with any `X-User-Id` header is passed to a bring-your-own
harness as an authenticated user. The harness (a2a-go v2.7.0 `ExecutorContext.User`)
sees `User{Name: <the header value>, Authenticated: true}`, with no
`Authorization` header, so it cannot tell an asserted identity from a verified
one.

**Reproduce.** A BYO harness that logs `request.User`; `POST
/agents/<ns>/<name>` with `X-User-Id: someone@example.com` and no other
credential.

**Expected.** `Authenticated: false` (or an attribute saying how the identity
was established) in insecure mode, so a harness that records decisions, such
as approvals at a human-in-the-loop pause, can refuse or label unverified
callers.

---

## 7. kagent-dev/kagent: a BYO agent's published card drops its capability extensions

**Summary.** A bring-your-own harness built on `go/adk/pkg/app` declared
`Capabilities.Extensions: [https://kagent.dev/extensions/hitl/v1]` in its
agent card and used the extension correctly (input-required with a
`tool_approval_request`, resumed by a `tool_approval_response`). The card
kagent publishes for the agent
(`/agents/<ns>/<name>/.well-known/agent-card.json`) has
`capabilities.extensions: null`, so a client cannot discover that the agent
pauses for approval; it worked only because our client activated the
extension with `A2A-Extensions` unprompted. The BYO documentation says BYO
cards declare no extension; the request is to carry the harness's declared
extensions through, or to let the Harness or Agent resource declare them.
