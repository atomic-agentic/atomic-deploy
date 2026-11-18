# Spikes on this workstation's k3s

The [design's spikes](../README.md#spikes-before-building) that can run
before atomic has `atomic serve`, on the workstation's single-node k3s, without
Istio and without agentgateway as the model gateway. Substrate's own router and
egress gateway still run on agentgateway, since kagent's charts choose it.

## Host preparation

[`host-setup.sh`](host-setup.sh) is the only step that needs root:

```sh
sudo ./host-setup.sh
export KUBECONFIG=~/.kube/k3s.yaml
```

| Change | Why |
| --- | --- |
| `docker: true` removed from `/etc/rancher/k3s/config.yaml`, with the image-GC overrides that protected Docker's store; parallel image pulls on | Substrate and kagent are documented on containerd, and containerd drop-ins are ignored under cri-dockerd |
| `/etc/rancher/k3s/registries.yaml` for `localhost:5000` | Spike images go to a local registry, not Docker's store |
| k3s v1.36.5+k3s1 → v1.37.2+k3s1, unit still disabled at boot | Pod certificates and cluster trust bundles are GA in 1.37, so Substrate needs no feature gates |
| `vllm` Deployment scaled to 0 | Its image, `local/flash-next-rocm-int4ple:prototype` (about 40 GiB), exists only in Docker's store |

It leaves SELinux enforcing and does not give k3s `selinux: true`, sets no
sysctls, and does not touch firewalld (the pod and service CIDRs are already in
the trusted zone).

**vLLM on containerd** would need its image imported into k3s's store
(`docker save … | sudo k3s ctr images import -`), about 40 GiB more on a root
filesystem with about 40 GiB free. Until that is solved, vLLM runs on k3s only
with the old configuration.

**Undo:**

```sh
sudo cp -p /etc/rancher/k3s/config.yaml.pre-substrate /etc/rancher/k3s/config.yaml
sudo rm /etc/rancher/k3s/registries.yaml
curl -fsSL https://raw.githubusercontent.com/k3s-io/k3s/v1.36.5%2Bk3s1/install.sh -o /tmp/k3s-install.sh
sudo INSTALL_K3S_VERSION=v1.36.5+k3s1 INSTALL_K3S_SKIP_ENABLE=true INSTALL_K3S_SKIP_SELINUX_RPM=true sh /tmp/k3s-install.sh
sudo restorecon -v /usr/local/bin/k3s && sudo systemctl restart k3s
kubectl -n vllm scale deployment vllm --replicas=1
```

## Local registry

A `registry:3` container listening on `127.0.0.1:5000` for builds and on
docker0 (`172.17.0.1:5000`) for pods. Substrate's `atelet` pulls actor images
from inside its pod, where `localhost` is the pod itself, so it gets
`--localhost-registry-replacement=172.17.0.1:5000`, as Substrate's kind setup
does with its own registry. Images are named `localhost:5000/<name>`.

## Spikes

| # | Spike | Question | Status |
| --- | --- | --- | --- |
| 1 | Platform | Do kagent 1.0.1 and Substrate 1.0.0 install and run Substrate's `counter` demo on k3s 1.37 with containerd and SELinux enforcing? Does a microVM worker pool run on `/dev/kvm`? Is `proxy_arp` needed outside kind? | Works on gVisor and on microVMs |
| 8 | Footprint | Memory and CPU of the platform before any actor, and per actor in each pool | Done |
| 4 | WebSocket through the router (`atomic-2h8u`) | Does an inbound WebSocket upgrade reach an actor through `atenet-router`? A plain echo actor first; Codex's exec-server only if that works | Works; a session cannot span a suspend |
| 5 | Codex's sandbox in gVisor (`atomic-k0dx`) | Can bubblewrap create its namespaces inside a gVisor actor? The [AWS spikes'](../../aws-lambda/spikes) sandbox probe, served over HTTP since actors have no exec API | Works with the right template and Codex profile |
| 7 | Snapshot and resume (`atomic-klse`) | Volume snapshot size and synchronous suspend and resume times with a large worktree in the actor's durable directory | Done, node-local restores only |
| 6 | kagent bridge | Can a bring-your-own harness, built from kagent's `byoa2a` example, show a gate as `input-required`, be suspended, and resume when answered? | Works, forwarding to a stand-in `atomic serve` |

| 3 | agentgateway as the model gateway | Can an actor reach a model through Substrate's egress gateway and agentgateway, holding no credential, with agentgateway authenticating the actor? | Works |

Spike 9 (one trace per run) waits for the observability stack in the cluster;
spike 2 for Istio.

Results go in [`results.md`](results.md), one section per spike, with versions,
commands and raw output.
