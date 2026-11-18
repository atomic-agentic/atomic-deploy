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
| 7 | Snapshot and resume | 527 MB of incompressible worktree in 20 001 files: synchronous suspend 2.2 to 2.7 s, snapshot about the worktree's size, resume 0.24 s (memory) or 0.58 s (volumes). Restores came from the node-local copy |
| 6 | kagent bridge | **Works on kagent's side.** A bring-your-own harness on kagent's Go SDK pauses at a gate as a HITL `tool_approval_request`; kagent stores it, suspends the actor while it waits, and the answer resumes it and completes the task in 0.17 s. The harness is told the caller is `Authenticated:true` from a bare `X-User-Id` header |

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
- **A 60 s request timeout** on the actor routes, and 60 s of request parking.
  Upstream's Envoy default is 5 minutes. An upgraded WebSocket outlives it; a
  long plain HTTP request (a streamed model turn) has not been tried.

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
- Not yet tried: the bridge forwarding to an `atomic serve` stand-in through
  an egress grant, a rejection, and a gate outliving kagent's 7-day session TTL.

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

Snapshots are not deduplicated against earlier ones in what was measured, and
random data does not compress, so a snapshot costs about the worktree's size.
Both resumes came from the node-local copy; restoring from rustfs alone, as on
another node, is not measured on one node.

## State left on the cluster

- Atespaces `ate-demo-counter`, `ate-demo-counter-microvm` and `spikes`, with
  their templates and actors, all suspended; kagent's `atomic-bridge-spike`
  Harness and Agent and one suspended session.
- WorkerPools `counter` (two gVisor workers) and `counter-microvm` (one), the
  `microvm` SandboxConfig and its assets in rustfs.
- In `ate-system`: the `router-client` pod, the `ws-client` ConfigMap and the
  `atenet-client` ServiceAccount.
- On the host: the `substrate-spike-registry` container. errai's containers
  are stopped; `lemonade-server` kept running throughout.
