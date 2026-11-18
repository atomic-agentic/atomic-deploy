# Spike results

Run on 2026-11-17 and 18 (UTC) on the workstation: Rocky Linux 10.2, kernel
6.12.0-211.74.1, SELinux enforcing, 24 threads, 60 GiB RAM, k3s v1.37.2+k3s1
on its embedded containerd 2.3.7. kagent 1.0.1 and Substrate 1.0.0 from
kagent's charts, with [`kagent/config.yaml`](kagent/config.yaml); the
Substrate source read is `agent-substrate/substrate` at `3d574499`. Manifests
are in [`manifests/`](manifests), images in [`images/`](images).

## Summary

| # | Spike | Answer |
| --- | --- | --- |
| 1 | Platform | **Works.** kagent and Substrate install on single-node k3s 1.37 with containerd and SELinux enforcing, without `selinux: true`, feature gates or `proxy_arp`. The counter demo keeps memory, rootfs and volume state across suspend and resume, and resumes on a different worker after its worker pod is deleted. The microVM pool is not done yet |
| 8 | Footprint | **Small.** About 660 MiB for the whole platform before any actor; about 56 MiB per idle gVisor worker; nothing for a suspended actor |
| 4 | WebSocket through the router | **Works on both paths,** the HTTPS route and the `CONNECT` tunnel, with 70 KB frames, to a suspended actor (the upgrade resumes it), and held open for 95 s, past the route's 60 s request timeout. Codex's exec-server itself is not tried yet |
| 5 | Codex's sandbox in gVisor | **bwrap needs capabilities the template must add, and never gets a network namespace.** With Substrate's default capabilities bwrap cannot map uids. With the capabilities Substrate allows adding, bwrap starts with user, IPC and PID namespaces, but `--unshare-net` always fails. Codex 0.169 passes `--unshare-net` for every sandboxed mode unless its network policy is enabled; the only mode that ran was `:danger-full-access` |
| 7 | Snapshot and resume | Small actor only so far: synchronous suspend 0.22 s, first request after resume 0.19 s, 0.09 s after resuming on a new worker. The large-worktree case is not done |
| 6 | kagent bridge | Not started |

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
- The `gvisor-default` SandboxConfig, default version `v7`.

What the spikes added on top:

- `atelet` gets `--localhost-registry-replacement=172.17.0.1:5000`
  ([`kagent/atelet-values.yaml`](kagent/atelet-values.yaml)); actor images are
  pushed to the `registry:3` container on `localhost:5000` and pulled by
  `atelet` from docker0. The golden snapshot of the first template was ready
  11 s after the template was created.
- The router's allowlist gets `atenet-client`, which `kubectl ate proxy`
  presents ([`kagent/substrate-values.yaml`](kagent/substrate-values.yaml)),
  and the `atenet-client` ServiceAccount, which kagent's install does not
  create. The list replaces kagent's, so it repeats the controller.

The [counter demo](manifests/counter-pool.yaml) ran on its own two-worker pool
with the image built by
[`images/substrate-demo.Dockerfile`](images/substrate-demo.Dockerfile) from
Substrate's vendored source:

```text
POST                     preserved memory count: 1 | file counter: 1 | rootfs counter: 1
POST                     ... 2 | 2 | 2
suspend --mode=sync      0.217 s
POST (resumes)           ... 3 | 3 | 3     0.189 s
POST                     ... 4 | 4 | 4     0.014 s
delete the worker pod, then POST
                         ... 5 | 5 | 5     0.088 s, on the replacement worker
```

Not needed on this host, though Substrate's kind script sets them: the
`ClusterTrustBundle` and `PodCertificateRequest` feature gates (GA in 1.37),
and `proxy_arp`. Router to actor and actor to router traffic works without
it; actor egress has not been exercised yet.

Not done: a microVM pool. kagent's install creates only the gVisor
SandboxConfig; a `microvm` one needs four guest assets (cloud-hypervisor,
virtiofsd, a Kata kernel and rootfs) fetched, checked against the digests in
Substrate's `sandboxconfig-microvm.yaml.tmpl` and staged into rustfs under
`microvm-assets/amd64/<sha256>`.

## 8. Footprint

| Measure | Value |
| --- | --- |
| Host memory available before k3s's workloads, after | about 13.0 GiB, about 14 GiB (page cache moves more than the platform) |
| k3s and containerd | about 950 MiB RSS |
| All pods after install, before any actor | about 660 MiB (`atelet` 158 MiB, Postgres 100 MiB, kagent controller 56 MiB, rustfs 47 MiB, each `ate-api-server` about 40 MiB) |
| An idle gVisor worker | about 56 MiB |
| A suspended actor | none in memory; its snapshot in rustfs |

Packing is by declared size, not count: each worker reports capacity for 1000
actors, and an actor takes its declared CPU and memory from its worker's
limits. The counter demo's template asks for 1000 millicores of a 1000
millicore worker, so it fills one; the probes ask for half. kagent's own
worker reports the whole node (24 000 millicores, 60 GiB) as its capacity.

## 4. WebSocket through the router

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

So the transport question in `atomic-2h8u` is answered: an exec-server
WebSocket can reach an actor, by the HTTPS route or by a `CONNECT` tunnel, which
is what Substrate's own Go client (`ConnectActorWithHeaders`) and
`kubectl ate proxy` use. Still to do: Codex's own exec-server
(`codex-exec-server`, a separate release asset) in an actor, with a session
across a suspend.

## 5. Codex's sandbox in a gVisor actor

[`images/probe`](images/probe)'s `/sandbox` runs Codex's release bwrap
(`rust-v0.169.0`, sha256-checked) with namespace sets including Codex's own
(`--unshare-user --unshare-ipc --unshare-pid --proc /proc`, a writable bind,
with and without `--unshare-net`), and installs an allow-all seccomp filter
with `NO_NEW_PRIVS`, as Codex does in-process.
[`images/codex-probe`](images/codex-probe)'s `/codex` runs Codex 0.169.0's
`codex sandbox -P <profile>` with a write inside the workspace and one to
`/etc`.

| | Docker on the host (runc, default seccomp) | Actor, default capabilities | Actor, every capability Substrate allows |
| --- | --- | --- | --- |
| Kernel | 6.12.0 | `4.19.0-gvisor` | `4.19.0-gvisor` |
| Effective capabilities | Docker's default | `AUDIT_WRITE`, `KILL`, `NET_BIND_SERVICE` | adds `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `FSETID`, `SETFCAP`, `SETGID`, `SETPCAP`, `SETUID`, `SYS_CHROOT` |
| `unshare(CLONE_NEWUSER)` | refused | ok | ok |
| Landlock | ABI 6 | not implemented (`ENOSYS`) | not implemented |
| seccomp filter with `NO_NEW_PRIVS` | ok | ok | ok |
| bwrap, Codex's flags, no network namespace | refused | `setting up uid map: Operation not permitted` | **ok** |
| bwrap, Codex's flags with `--unshare-net` | refused | `loopback: Failed RTM_NEWADDR` | `loopback: Failed RTM_NEWADDR` |
| `codex sandbox -P :workspace` | refused | `loopback: Failed RTM_NEWADDR` | `loopback: Failed RTM_NEWADDR` |
| `codex sandbox -P :read-only` | refused | same | same |
| `codex sandbox -P :danger-full-access` | runs, and writes `/etc` | runs, and writes `/etc` | runs, and writes `/etc` |

What it means:

- **gVisor's netstack cannot configure loopback in a new network namespace**,
  so no Codex mode that isolates the network can start, whatever the
  capabilities.
- **Codex skips `--unshare-net` only when its network policy is enabled** and
  no managed network proxy is configured (`bwrap_network_mode` in
  `codex-rs/linux-sandbox/src/linux_run_main.rs`). Then, with the capabilities
  added, its filesystem sandbox should start: the raw bwrap run with Codex's
  flags and no network namespace did. Not yet shown with the Codex binary:
  `-c sandbox_workspace_write.network_access=true` did not enable the network
  for the `:workspace` profile in 0.169, and the setting that does is still to
  find (likely a custom permissions profile).
- **Network isolation then moves to Substrate,** which enforces the actor's
  egress policy for every process in it alike: the harness and the commands
  it runs share one policy, and so share whatever the egress gateway injects.
- **Codex checks its bundled bwrap's digest before use** (`bundled bubblewrap
  digest mismatch` when replaced), which a runner image keeps for free.

## 7. Snapshot and resume

Only the small counter actor so far (times above). The large-worktree case,
a volume snapshot of a cloned repository in the actor's durable directory, is
still to run.

## 6. kagent bridge

Not started.

## State left on the cluster

The `ate-demo-counter` and `spikes` atespaces with their templates and actors
(all suspended except `my-counter-1`), the `counter` WorkerPool (two workers),
the `router-client` pod and `ws-client` ConfigMap in `ate-system`, and the
`atenet-client` ServiceAccount. The `substrate-spike-registry` container runs
on the host. errai's containers are stopped.
