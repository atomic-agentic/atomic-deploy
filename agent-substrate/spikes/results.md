# Spike results

Run on 2026-11-17 and 18 (UTC) on the workstation: Rocky Linux 10.2, kernel
6.12.0-211.74.1, SELinux enforcing, 24 threads, 60 GiB RAM, k3s v1.37.2+k3s1
on its embedded containerd 2.3.7. kagent 1.0.1 and Substrate 1.0.0 from
kagent's charts, with [`kagent/config.yaml`](kagent/config.yaml); the
Substrate source read is `agent-substrate/substrate` at `3d574499`, kagent's
is `v1.0.1` (`558e3d7`), Codex's is `rust-v0.169.0`. Manifests are in
[`manifests/`](manifests), images in [`images/`](images), clients in
[`tools/`](tools).

## Summary

| # | Spike | Answer |
| --- | --- | --- |
| 1 | Platform | **Works, on gVisor and on microVMs.** kagent and Substrate install on single-node k3s 1.37 with containerd and SELinux enforcing, without `selinux: true`, feature gates or `proxy_arp`. The counter demo keeps memory, rootfs and volume state across suspend and resume on both sandbox classes, and resumes on a different worker after its worker pod is deleted |
| 8 | Footprint | **Small.** About 660 MiB for the whole platform before any actor; 16 to 60 MiB per idle worker; nothing in memory for a suspended actor |
| 4 | Exec-server through the router | **Works.** A WebSocket passes both router paths, resumes a suspended actor and outlives the router's 60 s request timeout; Codex's own exec-server runs commands in a gVisor actor through it. A suspend during a command closes the session and the command does not survive |
| 5 | Codex's sandbox in gVisor | **Works with a runner template and a Codex profile made for it.** With the capabilities Substrate allows adding and a permission profile that extends `:workspace` with the network enabled, Codex 0.169's own sandbox runs in a gVisor actor: workspace writes succeed, writes to `/etc` are refused. Every mode that isolates the network fails, because gVisor cannot configure loopback in a new network namespace; network isolation is Substrate's egress policy instead |
| 7 | Snapshot and resume | A shallow clone of Kubernetes (334 MB, 32 021 files) made inside the actor: synchronous suspend 1.27 s, 91 MB snapshot, restore from rustfs alone 1.10 s, `git status` clean. Incompressible data is the worst case: 527 MB suspends in 2.2 to 2.7 s, its snapshot is as large, and restores in 1.4 to 1.6 s from rustfs |
| 6 | kagent bridge | **Works end to end with a stand-in `atomic serve`.** A bring-your-own harness on kagent's Go SDK pauses at a gate as a HITL `tool_approval_request`; kagent stores it, suspends the actor while it waits, and the answer resumes it in 0.17 to 0.27 s. The bridge forwards the decision through an egress grant, and `atomic serve` receives a Substrate actor JWT naming kagent's session. The person is only an assertion: a bare `X-User-Id` header reaches the harness as `Authenticated:true` |
| 2 | Istio ambient beside Substrate | **Works, with Substrate's egress gateway enrolled by pod.** Istio 1.32.0 ambient installs on k3s with SELinux enforcing. Strict mTLS for the trusted namespaces refuses Substrate's plaintext egress hop until `atenet-egress`'s pods join the mesh; then the hop is mTLS with the egress gateway's SPIFFE identity, which an AuthorizationPolicy can require. Connections pooled before a policy change keep their old admission |
| 3 | agentgateway as the model gateway | **Works.** An actor holding only a placeholder token calls the chat API; Substrate's egress gateway injects an actor JWT, agentgateway validates it against Substrate's IdP, allows it by atespace with CEL, injects the provider key and reaches Lemonade on the host. No token or a forged token gets 401; an actor in another atespace gets 403 |

## Host preparation

[`host-setup.sh`](host-setup.sh)'s first version passed the kubelet
`--max-parallel-image-pulls`, which exists only as a configuration-file field:
k3s restarted every few seconds, and the script reported success because the
node object kept its old `Ready` status. Fixed: the flag is gone and the script
waits for the service and for the node's new kubelet version.

k3s 1.37 installs the Gateway API CRDs itself (v1.6.3, a `helm-install`
job), within agentgateway 1.7's supported range.

## 1. Platform

`kagent --config kagent/config.yaml install --profile minimal` took 7 min 36 s,
mostly image pulls. It created:

- Substrate in `ate-system`: two `ate-api-server` and two `ate-idp-server`
  replicas, `ate-controller`, the `atelet` DaemonSet, `atenet-router` and
  `atenet-egress` on agentgateway, `k8s-credential-provider`, rustfs (bucket
  `ate-snapshots`), and the pod-certificate controller in its own namespace.
- kagent in `kagent`: controller, UI, tools server, a PostgreSQL pod, and the
  `kagent-default` WorkerPool (one gVisor worker).
- The `gvisor-default` SandboxConfig, default version `v7`. No microVM one.

What the spikes added on top:

- `atelet` gets `--localhost-registry-replacement=172.17.0.1:5000`
  ([`kagent/atelet-values.yaml`](kagent/atelet-values.yaml)); actor images are
  pushed to the `registry:3` container on `localhost:5000` and pulled by
  `atelet` from docker0. The first golden snapshot was ready 11 s after its
  template was created.
- The router's allowlist gets `atenet-client`, which `kubectl ate proxy`
  presents ([`kagent/substrate-values.yaml`](kagent/substrate-values.yaml)),
  and the `atenet-client` ServiceAccount, which kagent's install does not
  create. The list replaces kagent's, so it repeats the controller.

The [counter demo](manifests/counter-pool.yaml), built by
[`images/substrate-demo.Dockerfile`](images/substrate-demo.Dockerfile) from
Substrate's vendored source, on a two-worker gVisor pool:

```text
POST                     preserved memory count: 1 | file counter: 1 | rootfs counter: 1
POST                     ... 2 | 2 | 2
suspend --mode=sync      0.217 s
POST (resumes)           ... 3 | 3 | 3     0.189 s
POST                     ... 4 | 4 | 4     0.014 s
delete the worker pod, then POST
                         ... 5 | 5 | 5     0.088 s, on the replacement worker
```

And on a one-worker [microVM pool](manifests/counter-microvm-pool.yaml)
(Cloud Hypervisor v53.0 with Kata 4.3.1's guest kernel and rootfs):

```text
golden snapshot          21 s after the template was created
POST                     ... 1 | 1 | 1
POST                     ... 2 | 2 | 2     0.013 s
suspend --mode=sync      0.50 s, 20 MB snapshot (3 MB for the same actor on gVisor)
POST (resumes)           ... 3 | 3 | 3     0.207 s
POST                     ... 4 | 4 | 4     0.016 s
```

The microVM class needed what kagent's install does not do: the four guest
assets (`cloud-hypervisor`, `virtiofsd`, `vmlinux`, `rootfs.img`) downloaded
from their releases, checked against the digests in Substrate's
`sandboxconfig-microvm.yaml.tmpl` (all four matched), staged into rustfs under
`microvm-assets/amd64/<sha256>` with the aws-cli image Substrate's installer
pins, and the [rendered SandboxConfig](manifests/sandboxconfig-microvm.yaml)
applied. `atelet` advertises `ate.dev/kvm` on this node without further
setup.

Not needed on this host, though Substrate's kind script sets them: the
`ClusterTrustBundle` and `PodCertificateRequest` feature gates (GA in 1.37),
and `proxy_arp`. Router-to-actor and actor-to-control-plane traffic (the
kagent bridge's bootstrap through the egress gateway) work without it.

## 8. Footprint

| Measure | Value |
| --- | --- |
| Host memory available before the install, after everything | about 13.0 GiB, about 12 GiB |
| k3s and containerd | about 950 MiB RSS |
| All pods after install, before any actor | about 660 MiB (`atelet` 158 MiB, Postgres 100 MiB, kagent controller 56 MiB, rustfs 47 MiB, each `ate-api-server` about 40 MiB) |
| An idle worker | about 56 MiB (gVisor), 16 MiB (microVM) |
| A suspended actor | none in memory; its snapshot in rustfs |

Packing is by declared size, not count: each worker reports capacity for 1000
actors, and an actor takes its declared CPU and memory from its worker's
limits. The counter demo's template asks for 1000 millicores of a 1000
millicore worker, so it fills one; the probes ask for half. kagent's own
worker reports the whole node (24 000 millicores, 60 GiB) as its capacity.

## 4. Exec-server through the router

[`images/probe`](images/probe) serves a WebSocket echo at `/ws`;
[`tools/ws-client.py`](tools/ws-client.py) sends a 5-byte and a 70 000-byte
message and checks that both come back.

| Path | Client | Result |
| --- | --- | --- |
| `CONNECT` tunnel (router 8444, TLS) | `kubectl ate proxy` on the host | 101, both echoes, close |
| The same, actor suspended first | `kubectl ate proxy` | 101 after the router resumed it, both echoes |
| The same, held 95 s with messages at 0, 30, 65 and 95 s | `kubectl ate proxy` | every message echoed |
| HTTPS route (router 443 → 8443), `ate-target-actor: spikes/probe-1` | [`router-client`](manifests/router-client.yaml) pod with `atenet-client`'s pod certificate | `GET` 200; WebSocket 101, both echoes |
| HTTPS route from a pod running as `default` | same pod, other service account | connection reset |

Then Codex's own exec-server ([`images/exec-server`](images/exec-server):
the release's standalone `codex-exec-server`, digest-checked, listening on
8080 beside the probe), driven by [`tools/exec-client.py`](tools/exec-client.py)
through the `CONNECT` path to port 8080:

| Case | Result |
| --- | --- |
| `initialize`, `initialized`, `process/start sh -c 'echo hello from $(uname -r)'`, `process/read` | `hello from 4.19.0-gvisor`, exit 0, 0.09 s; `sandboxType: none` |
| The actor suspended, then a new session | the upgrade resumed it; the command ran |
| A command sleeping 15 s, actor suspended 4 s in | suspend took 0.54 s and was not refused; the client's connection closed; after the resume the command's marker file was never written |

exec-server "terminates any remaining managed processes" when a connection
closes (its README), and a suspend closes it. So a session cannot span a
suspend: atomic suspends between steps only, and a dropped session is a failed
attempt.

What the router configuration in kagent's charts shows
(`atenet-router-agentgateway-config`):

- **Clients are authenticated.** A CEL rule admits only listed SPIFFE IDs,
  which the reset above confirms. Upstream's note that "the agentgateway
  dataplane does not authenticate clients yet" does not apply to these charts.
- **Only TLS listeners.** 8443 (HTTPS routes) and 8444 (`CONNECT`); the pod
  declares 8080 and 8081, but nothing listens there.
- **A configured `requestTimeout: "60s"` that cuts nothing.** Requests that
  answer only after 90 s and after 330 s, and a response streamed for 90 s,
  all completed with 200 on both the HTTPS route and the `CONNECT` path
  ([`images/probe`](images/probe)'s `/slow` and `/stream`). Request parking is
  60 s. Upstream's Envoy route timeout is 5 minutes, so the ceiling depends on
  the data plane; atomic should not rely on either.

## 5. Codex's sandbox in a gVisor actor

[`images/probe`](images/probe)'s `/sandbox` runs Codex's release bwrap
(sha256-checked) with namespace sets including Codex's own
(`--unshare-user --unshare-ipc --unshare-pid --proc /proc`, a writable bind,
with and without `--unshare-net`), and installs an allow-all seccomp filter
with `NO_NEW_PRIVS`, as Codex does in-process.
[`images/codex-probe`](images/codex-probe)'s `/codex` runs Codex 0.169.0's
`codex sandbox -P <profile>` with a write inside the workspace (`/work`) and
one to `/etc`.

| | Docker on the host (runc, default seccomp) | Actor, default capabilities | Actor, every capability Substrate allows |
| --- | --- | --- | --- |
| Kernel | 6.12.0 | `4.19.0-gvisor` | `4.19.0-gvisor` |
| Effective capabilities | Docker's default | `AUDIT_WRITE`, `KILL`, `NET_BIND_SERVICE` | adds `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `FSETID`, `SETFCAP`, `SETGID`, `SETPCAP`, `SETUID`, `SYS_CHROOT` |
| `unshare(CLONE_NEWUSER)` | refused | ok | ok |
| Landlock | ABI 6 | not implemented (`ENOSYS`) | not implemented |
| seccomp filter with `NO_NEW_PRIVS` | ok | ok | ok |
| bwrap, Codex's flags, no network namespace | refused | `setting up uid map: Operation not permitted` | ok |
| bwrap, Codex's flags with `--unshare-net` | refused | `loopback: Failed RTM_NEWADDR` | `loopback: Failed RTM_NEWADDR` |
| `codex sandbox -P :workspace` | refused | `loopback: Failed RTM_NEWADDR` | `loopback: Failed RTM_NEWADDR` |
| `codex sandbox -P :read-only` | refused | same | same |
| `codex sandbox -P actor`, a profile extending `:workspace` with `network.enabled = true` | refused | `setting up uid map` | **workspace write ok; `/etc` write refused: `Read-only file system`** |
| `codex sandbox -P :danger-full-access` | runs, writes `/etc` | runs, writes `/etc` | runs, writes `/etc` |

What it means:

- **A Codex runner in an actor is three things together:** the image (Codex
  and its bundled bwrap, which Codex checks by digest before every run:
  replacing it gives `bundled bubblewrap digest mismatch`), the template (the
  capabilities above, which Substrate's admission allows), and Codex's
  configuration (a permission profile with the network enabled):

  ```toml
  [permissions.actor]
  extends = ":workspace"
  network.enabled = true
  ```

  `-c sandbox_workspace_write.network_access=true` does not do this in 0.169;
  the profile does.
- **gVisor's netstack cannot configure loopback in a new network namespace**,
  so no Codex mode that isolates the network starts. Codex skips
  `--unshare-net` only when the profile's network is enabled and no managed
  network proxy is configured (`bwrap_network_mode` in
  `codex-rs/linux-sandbox/src/linux_run_main.rs`).
- **Network isolation moves to Substrate,** which enforces the actor's egress
  policy for every process in it alike: Codex and the commands it runs share
  one policy, and so share whatever the egress gateway injects.

## 6. kagent bridge

[`images/kagent-bridge`](images/kagent-bridge) is a bring-your-own harness on
kagent's Go SDK at v1.0.1, after kagent's own `go/core/test/byoa2a`: the first
message on a task "starts a run" (a stub; no `atomic serve` yet) and pauses
with an input-required status carrying a HITL `tool_approval_request` for one
gate; the answer is validated against the pending request with the SDK's
`ValidateToolApprovalResponse`. [`manifests/kagent-bridge.yaml`](manifests/kagent-bridge.yaml)
declares the Harness (`byo`, image by digest) and an Agent on kagent's default
worker pool; [`tools/kagent-gate.py`](tools/kagent-gate.py) drives it through
kagent's public A2A gateway (`POST /agents/kagent/atomic-bridge-spike`,
`A2A-Version: 1.0`, `A2A-Extensions: https://kagent.dev/extensions/hitl/v1`):

```text
task 01a1ec41-…: TASK_STATE_INPUT_REQUIRED
gate as kagent stored it: {"hint": "Approve sending the draft reply?", "tools": [{"args": {"gate": "send-reply",
  "run": "stub-run-1"}, "call_id": "gate-send-reply", "id": "gate-send-reply", "name": "atomic.gate"}],
  "type": "tool_approval_request"}
actor right after the pause: ACTOR_STATE_SUSPENDING
actor after 20 s: ACTOR_STATE_SUSPENDED
after the answer (0.17 s, resume included): TASK_STATE_COMPLETED
bridge says: Gate gate-send-reply decided: approved=true reason="". Caller as the harness sees it:
  user={Name:sam@example.com Authenticated:true Attributes:map[]} x-user-id=sam@example.com
  ate-target-actor=kagent/session-01a1ec41-…
```

What it shows:

- **The bridge's shape works:** kagent's SDK does the bootstrap (through
  Substrate's egress gateway, with an actor JWT swapped in for a placeholder)
  and the task store; the harness is a page of Go. The golden snapshot built in
  about 20 s.
- **kagent quiesces the actor at the gate by itself,** so a gate that waits
  for days costs nothing while it waits, and the answer resumes it in well
  under a second.
- **Who answered is an assertion.** In kagent's default `insecure` mode the
  client's own `X-User-Id` header becomes `User{Name: sam@example.com,
  Authenticated: true}` in the harness, with no `Authorization` header. A gate
  decision from kagent proves only that something reached kagent's gateway.
Then the decision forwarded. The bridge POSTs it to `ATOMIC_URL` with
`Authorization: Bearer kagent-placeholder`; a kagent
[`EgressPolicy`](manifests/kagent-bridge.yaml) grants that origin with
`authentication.actorJwt` (audience `atomic-serve`); the stand-in
([`manifests/atomic-serve-stub.yaml`](manifests/atomic-serve-stub.yaml): the
probe on plain runc, reporting each request) received, for a rejection:

```text
POST /v1/gates/decisions
authorization: Bearer <ES256 JWT, kid 1>
  iss  https://idp.ate-system.svc
  sub  actor/kagent/session-01a1ec59-02a4-7802-a8e6-4b5dc3dec330
  aud  ["atomic-serve"]          15-minute lifetime
  actor.ate.dev  {atespace: kagent, actorName: session-01a1ec59-…, actorUID: 8ddb25b3-…}
body {"gate": "gate-send-reply", "approved": false, "reason": "not today",
      "task": "01a1ec59-03d6-…", "context": "01a1ec59-02a4-…",
      "principal": "user={Name:sam@example.com Authenticated:true …} x-user-id=sam@example.com …"}
```

So `atomic serve` can verify that a call comes from kagent's actor for one
session (the actor name carries kagent's context ID), with Substrate's IdP as
the issuer, and nothing secret ever enters the actor. Who decided is still
only what kagent was told.

Not tried: a gate outliving kagent's 7-day session TTL.

## 2. Istio ambient beside Substrate

Istio 1.32.0 from `https://blob.istio.io/istio-release/charts` (the older
`istio-release.storage.googleapis.com` index stops before 1.32, and the
`gcr.io/istio-release` OCI path has no 1.32 charts): `base`, `istiod` and
`cni` with `profile=ambient` (and `global.platform=k3s` on `cni`), `ztunnel`.
istio-cni chained itself into k3s's `10-flannel.conflist` and installed
in-pod rules with `iptables-nft-restore`; nothing needed for SELinux. Footprint:
istiod 49 MiB, istio-cni 27 MiB, ztunnel 7 MiB.

Substrate's namespaces and kagent's (where kagent's worker pods run) stay out
of the mesh. [`manifests/ambient.yaml`](manifests/ambient.yaml) holds the
policies and the enrollment commands.

| Step | Setup | Result |
| --- | --- | --- |
| T1 | `atomic` enrolled, permissive | the bridge's decision reaches the stand-in |
| T2 | `PeerAuthentication` STRICT in `atomic` | **still reached it**, on a connection Substrate's egress gateway had pooled under T1. After restarting the egress gateway: refused, the bridge gets HTTP 503 `connection reset` |
| — | the same, from a plain pod outside the mesh | connection reset; ztunnel logs it |
| T3 | `atenet-egress`'s pods enrolled by pod label, plus an additive NetworkPolicy for ambient's probe address `169.254.7.127` | reaches it again over HBONE |
| T4 | AuthorizationPolicy: only `cluster.local/ns/ate-system/sa/atenet-egress` | the bridge's decision passes, `src.identity="spiffe://cluster.local/ns/ate-system/sa/atenet-egress"`; a mesh pod as `default/default` is refused with `401 Unauthorized` |
| T5 | `agentgateway-system` enrolled, STRICT | an actor's model call through the egress gateway returns 200 in 1.5 s; plaintext from a pod outside the mesh is reset |

What it shows:

- **The trusted tier can be strict.** The only Substrate component it needs
  in the mesh is the egress gateway, enrolled by pod label, so worker pods
  and actors never see ambient's redirection.
- **Enrolling it takes an extra NetworkPolicy.** Substrate's default-deny in
  `ate-system` would drop ambient's kubelet probes, which arrive from
  `169.254.7.127`; an additive policy for that address is enough.
- **Inbound to the egress gateway is unchanged:** workers outside the mesh
  still reach it in plaintext, because `ate-system` is not strict.
- **Policy changes do not reach pooled connections.** The egress gateway
  reuses upstream connections, and ztunnel judges a connection when it opens
  (and logs it when it closes), so tightening a policy takes a restart of the
  egress gateway to take effect.
- **agentgateway 1.7 works behind ztunnel on Istio 1.32** as an ordinary
  enrolled workload, although its compatibility table stops at 1.31. Its own
  mesh integration (`istio.enabled`, HBONE in the proxy) was not needed for
  L4 mTLS and was not tried.

## Three findings, examined

Three results from the spikes above mattered enough to the design to chase to
their cause. Each was rerun on its own, with the source of the component
responsible read at the version installed.

### Pooled connections and mesh policy

In spike 2's T2, strict mTLS in `atomic` at first let Substrate's plaintext
egress hop through. Two experiments, with an actor posting to the stand-in
every 15 s ([`tools/egress-loop.py`](tools/egress-loop.py) through
exec-server), a policy change at about 62 s, and a 100 s pause near the end:

| | E1b: egress gateway in the mesh, revoke its identity in the AuthorizationPolicy | E1a: egress gateway outside the mesh, `atomic` PERMISSIVE → STRICT |
| --- | --- | --- |
| 0 to 60 s | 200 | 200 |
| After the change | **503 from the next request (75 s) on** | **200 for every request through 165 s** |
| After the 100 s pause | 503 | 503 |
| ztunnel | closed the pooled connection at the instant of the change: `connection closed due to policy change` (it had been open 62 s) | logged the pooled connection's end only when it closed, 265 s after it opened |

Why:

- **Substrate's egress gateway pools upstream connections** on agentgateway's
  defaults: `pool_idle_timeout` 90 s and no limit on idle connections
  (`crates/agentgateway/src/lib.rs` at v1.7.0); its configuration sets no pool
  options. The router, by contrast, sets `poolMaxSize: 0` and keeps none.
- **ztunnel re-evaluates authorization on open connections** when an
  AuthorizationPolicy changes, but in Istio 1.32.0 it did not close a
  plaintext connection admitted while the namespace was PERMISSIVE when
  PeerAuthentication turned STRICT.

So a stale admission is limited to plaintext from outside the mesh, and it
lasts as long as the egress gateway keeps the connection busy at least every
90 s: indefinitely on a busy path, 90 s on an idle one. With the egress gateway
enrolled (decision 4), revocation is immediate. What follows for the
deployment:

- Enroll `atenet-egress` and apply strict mTLS before the first actor traffic,
  not after; if a mesh policy is tightened while plaintext is in use, restart
  `atenet-egress`.
- `atomic serve` checks the actor JWT on every request, so its own
  authorization never depends on how old a connection is.

### Idle actors and capacity

`spikes-other`'s golden build in spike 3 waited until running actors were
suspended. What Substrate 1.0 does:

- **No idle suspend.** The API has a worker-proposed suspend
  (`WorkerService.RequestActorSuspend`, "only the Worker can observe what makes
  an Actor worth reclaiming"), and `atelet` forwards it, but nothing produces
  the signal: `internal/ateworker/suspend` has no importers at `3d574499`. The
  architecture doc leaves suspend to "the user (or the higher-level system)".
  kagent suspends its own session actors at each pause.
- **No eviction.** E2a: with both counter workers held by idle running
  actors, a request to a suspended actor parked for the router's budget and
  failed: `503 no worker has room for the actor` after 60.0 s (kagent's charts
  set the budget to 60 s; upstream's default is 5 s). Neither idle actor was
  touched.
- **Golden builds fail out of sight.** E2b: a template created while the
  workers were full showed no golden tag, no error and an empty `status`
  through the API; `ate-api-server`'s log had `Failed to reconcile actor
  template, requeueing … AssignWorker: no worker has room for the actor`, 20
  times in 3 minutes. Freed, it was ready in 10 s.

What follows for atomic: every actor it resumes it also suspends (after each
step, synchronously before a gate, on an idle timer, and at start-up for any
actor in its atespaces that no live attempt owns, since a crashed control
plane leaves its actors running); `503 no worker has room` is a placement
failure to retry with backoff, not an attempt failure; and a template counts
as ready only when its golden tag appears, within a deadline atomic sets.

### Request timeouts through the router

Requests of 90 s and 330 s completed through the router although its
configuration says `requestTimeout: "60s"`. Why, and what does end a long
request:

- **That timeout is not on actor traffic.** It sits in the route's
  `substrateIngress` policy, whose `policies` apply to the "Backend that
  receives ResumeActor calls" (`crates/agentgateway/src/http/substrate/ingress.rs`
  at v1.7.0): it bounds the router's call to Substrate's API to resume the
  actor. The actor route sets no timeout, and agentgateway applies none by
  default.
- **The data planes differ.** Upstream's Envoy router has a 5-minute default
  route timeout (`--route-timeout`, `cmd/atenet/internal/router/xds.go`);
  kagent's charts, on agentgateway, have no ceiling.
- **A router restart ends them.** E3a: with two 120 s requests in flight, the
  router was restarted 20 s in. About 6 s later
  (`drain duration expired with pending connections, forcefully shutting
  down`), both ended: the silent one with `RemoteDisconnected`, the streamed
  one after 6 of 24 lines. Read as raw chunked data the stream raised
  `IncompleteRead`; read line by line, as streaming clients commonly do, it
  looked like a normal end with status 200.

What follows for atomic: no attempt depends on one long request. Starting an
attempt is short and returns an ID; progress comes by polling or by a stream
whose events carry sequence numbers and an explicit final event, so a cut is
detected and the client reconnects from the last event; every attempt has a
deadline atomic enforces itself. An exec-server WebSocket is presumably cut by
a router restart in the same way (not tested), which gap 5 already treats as a
failed attempt.

## 3. agentgateway as the model gateway

agentgateway v1.7.0 on its own controller (`agentgateway-crds` and
`agentgateway` charts), with [`manifests/ai-gateway.yaml`](manifests/ai-gateway.yaml):
a Gateway `ai-gateway`, an `AgentgatewayBackend` for Lemonade on the host
(OpenAI-compatible, through a selectorless `models/lemonade` Service on
docker0) holding the provider key from a Secret, an HTTPRoute, and an
`AgentgatewayPolicy` with JWT authentication (issuer
`https://idp.ate-system.svc`, audience `ai-gateway`, Substrate's JWKS inline)
and CEL authorization `jwt["actor.ate.dev"].atespace == "spikes"`.

The actor's [egress policy](manifests/worktree-git-egress.yaml) has an `http`
rule for `ai-gateway.agentgateway-system.svc.cluster.local:80` that replaces
`Authorization` with an actor JWT for audience `ai-gateway`.

| Caller | Credential sent | Result |
| --- | --- | --- |
| `wt-git` actor (atespace `spikes`), from inside the actor | `Bearer placeholder` | 200 in 12.8 s; model replaced by the backend's (`Qwen3.6-35B-A3B-MTP-GGUF-UD-Q4_K_XL`); usage 16 in, 20 out |
| `other-1` actor (atespace `spikes-other`), same egress policy | `Bearer placeholder` | 403 `authorization failed` |
| A pod outside Substrate | none | 401 `authentication failed` |
| A pod outside Substrate | a forged ES256 JWT with the right issuer, audience and claims | 401 `authentication failed` |

agentgateway's metrics on 15020 carried it: `agentgateway_gen_ai_client_token_usage_sum`
by token type and model (16 input, 20 output) and `agentgateway_requests_total`
with `status="401", reason="JwtAuth"`. Prompts and replies were not in them.

Notes:

- The JWKS is inlined, so a key rotation in Substrate's IdP would break it. A
  remote JWKS (`https://idp.ate-system.svc/openid/v1/jwks`) needs agentgateway
  to trust Substrate's servicedns CA through a backend with TLS.
- The hop from Substrate's egress gateway to agentgateway is plain HTTP inside
  the cluster; Substrate's egress trusts only public CAs for `https` rules.
  Spike 2 makes it mTLS by enrolling the egress gateway's pods.
- Substrate never suspends an idle actor by itself: the `spikes-other`
  template's golden build waited, without an error, until two running actors
  that filled both workers were suspended, then finished in 3 s.

## 7. Snapshot and resume

[`manifests/worktree-volumes-template.yaml`](manifests/worktree-volumes-template.yaml)
and [`worktree-memory-template.yaml`](manifests/worktree-memory-template.yaml):
the exec-server image with a durable directory at `/work`, filled through
exec-server with 20 000 files of 1 to 32 KiB and one 200 MiB file, all random
bytes (527 MB, incompressible: a worst case).

| | `SNAPSHOT_FIDELITY_VOLUMES` (kagent's) | `SNAPSHOT_FIDELITY_MEMORY` (Substrate's default) |
| --- | --- | --- |
| Filling `/work` in the actor | 3.3 s | 2.5 s |
| `suspend --mode=sync` | 2.24 s | 2.65 s |
| First request after the suspend | 0.58 s (the process cold-starts) | 0.24 s |
| After resume | 527 MB, 20 001 files | 527 MB |
| Snapshot in rustfs | 524 MB | 530 MB |

Then a realistic worktree ([`manifests/worktree-git-template.yaml`](manifests/worktree-git-template.yaml),
volumes fidelity): a shallow clone of `kubernetes/kubernetes` made inside the
actor through an [egress policy](manifests/worktree-git-egress.yaml) that
allows only `github.com:443` as TLS passthrough (a host outside it did not
resolve; github.com returned 200).

| Measure | Value |
| --- | --- |
| `git clone --depth 1` in the actor | 16.4 s |
| Worktree | 334 MB, of which `.git` 52 MB; 32 021 files |
| `suspend --mode=sync` | 1.27 s |
| Snapshot in rustfs | 91 MB (source compresses) |
| First request after the local copy was dropped (restore from rustfs) | 1.10 s |
| After restore | 334 MB, 32 021 files, `git status` clean; the first `git status` 2.2 s against 0.45 s before |

Restore from rustfs alone, for the random worktree too, with `atelet`'s
`--snapshot-upload-delay=30s --snapshot-detach-delay=90s`
([`kagent/atelet-values.yaml`](kagent/atelet-values.yaml), spike-only; the
defaults are 5 and 30 minutes): the local copy was dropped 90 s after the
suspend, then the first request took 1.61 s (memory fidelity) and 1.37 s
(volumes), with all 527 MB back.

## State left on the cluster

- Istio 1.32.0 ambient in `istio-system`; `atomic` and `agentgateway-system`
  enrolled and strict; `atenet-egress` patched with the ambient pod label, and
  the `ambient-probes` NetworkPolicy in `ate-system`.
- agentgateway v1.7.0 in `agentgateway-system` with the `ai-gateway` Gateway,
  the `models` namespace pointing at Lemonade, and the `atomic` namespace with
  the `atomic-serve` stand-in.
- Atespaces `ate-demo-counter`, `ate-demo-counter-microvm`, `spikes` and
  `spikes-other`, with
  their templates and actors, all suspended; kagent's `atomic-bridge-spike`
  Harness and Agent and one suspended session.
- WorkerPools `counter` (two gVisor workers) and `counter-microvm` (one), the
  `microvm` SandboxConfig and its assets in rustfs.
- In `ate-system`: the `router-client` pod, the `ws-client` ConfigMap and the
  `atenet-client` ServiceAccount.
- On the host: the `substrate-spike-registry` container. errai's containers
  are stopped; `lemonade-server` kept running throughout.
