#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Prepare this workstation's k3s for the Agent Substrate deployment. The only
# step that needs root; up.sh, after it, runs as you.
#
#   sudo ./host-setup.sh
#
# What it changes, and how to undo it, is in README.md beside it:
#   - /etc/rancher/k3s/config.yaml: drops `docker: true` (kubelet moves from
#     cri-dockerd to k3s's embedded containerd) and the image-GC overrides that
#     protected Docker's store; enables parallel image pulls. The old file is
#     kept as config.yaml.pre-substrate.
#   - /etc/rancher/k3s/registries.yaml: the local registry on localhost:5000.
#   - k3s from v1.36.5+k3s1 to v1.37.2+k3s1, with the installer from that tag.
#     The unit stays disabled at boot, as it is now.
#   - Scales the vllm Deployment to 0: its image is only in Docker's store.
#
# What it leaves alone: SELinux stays enforcing and k3s does not get
# `selinux: true` (Substrate's nested runsc needs the unconfined spc_t label
# that pods get without it); no sysctls; the k3s-selinux package; firewalld
# (the pod and service CIDRs are already trusted).
set -euo pipefail

K3S_VERSION=v1.37.2+k3s1
CONFIG=/etc/rancher/k3s/config.yaml
BACKUP=$CONFIG.pre-substrate

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
user=${SUDO_USER:?Run through sudo from your own account}
home=$(getent passwd "$user" | cut -d: -f6)

# 1. Configuration. Back up once, so a second run never overwrites the original.
[[ -e $BACKUP ]] || cp -p "$CONFIG" "$BACKUP"
cat > "$CONFIG" <<'EOF'
# Agent Substrate deployment (atomic-deploy agent-substrate). The previous
# file, with `docker: true` for the vLLM prototype, is config.yaml.pre-substrate.
write-kubeconfig-mode: "0600"
# No ingress or load balancer.
disable:
  - traefik
  - servicelb
kubelet-arg:
  # Substrate's install pulls several images at once; serial pulls make the
  # last ones miss their readiness deadlines (Substrate's kind script does the
  # same). The kubelet has no flag for maxParallelImagePulls, only a config-file
  # field, so parallel pulls are unbounded here.
  - serialize-image-pulls=false
EOF
cat > /etc/rancher/k3s/registries.yaml <<'EOF'
# The deployment's local registry (up.sh starts it): a registry:3 container
# listening on 127.0.0.1:5000 and on docker0 (172.17.0.1:5000), plain HTTP.
mirrors:
  "localhost:5000":
    endpoint:
      - "http://localhost:5000"
EOF

# 2. k3s, with the installer from the release's own tag. Skip the SELinux RPM
#    step: k3s-selinux is already installed, and the installer picks el9 here.
installer=$(mktemp)
trap 'rm -f "$installer"' EXIT
curl -fsSL "https://raw.githubusercontent.com/k3s-io/k3s/${K3S_VERSION/+/%2B}/install.sh" -o "$installer"
systemctl stop k3s 2>/dev/null || true
INSTALL_K3S_VERSION=$K3S_VERSION \
INSTALL_K3S_SKIP_ENABLE=true \
INSTALL_K3S_SKIP_SELINUX_RPM=true \
    sh "$installer"
# Skipping the RPM step also skips the installer's relabel of the new binary;
# k3s-selinux's file contexts give it container_runtime_exec_t, as before.
restorecon -v /usr/local/bin/k3s
ls -Z /usr/local/bin/k3s | grep -q container_runtime_exec_t \
    || chcon -u system_u -r object_r -t container_runtime_exec_t /usr/local/bin/k3s
systemctl start k3s

kubectl() { /usr/local/bin/k3s kubectl "$@"; }
# The node object keeps its old Ready status while a new kubelet fails to start,
# so wait for the service itself and for the node to report the new version.
for _ in $(seq 100); do
    systemctl is-active -q k3s \
        && [[ $(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null) == "${K3S_VERSION}" ]] \
        && break
    sleep 3
done
systemctl is-active -q k3s || {
    echo "k3s is not running; see: journalctl -u k3s -n 50" >&2; exit 1; }
kubectl wait --for=condition=Ready node --all --timeout=300s

# 3. The vLLM prototype's image exists only in Docker's store; under containerd
#    its pod could only fail to pull. Keep it at zero until vLLM is moved.
if kubectl -n vllm get deployment vllm >/dev/null 2>&1; then
    kubectl -n vllm scale deployment vllm --replicas=0
fi

# 4. Substrate's pod certificates need these APIs, GA in Kubernetes 1.37.
missing=0
for r in podcertificaterequests clustertrustbundles; do
    kubectl api-resources --api-group=certificates.k8s.io -o name | grep -q "^$r\." \
        || { echo "missing API: $r.certificates.k8s.io" >&2; missing=1; }
done

# 5. Your kubeconfig.
install -d -m 0700 -o "$user" -g "$user" "$home/.kube"
install -m 0600 -o "$user" -g "$user" /etc/rancher/k3s/k3s.yaml "$home/.kube/k3s.yaml"

echo
kubectl get nodes -o wide
echo
kubectl api-resources --api-group=certificates.k8s.io
echo
if [[ $missing -ne 0 ]]; then
    echo "k3s is up, but the certificate APIs above are missing; see README.md." >&2
    exit 2
fi
echo "Done. As $user: export KUBECONFIG=$home/.kube/k3s.yaml"
