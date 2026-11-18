#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Remove what up.sh installed: the kagent bridge, kagent and Substrate (with
# every atespace, actor and snapshot in it), agentgateway, Istio, and the
# deployment's namespaces. Leaves k3s and host-setup.sh's changes, and the
# registry container with its images; host-setup.sh's README section says how
# to undo those.
#
#   ./down.sh          # asks first
#   ./down.sh --yes
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/k3s.yaml}
kubectl get nodes >/dev/null

if [[ ${1:-} != --yes ]]; then
    echo "This deletes kagent, Substrate (every actor and snapshot), agentgateway and Istio from"
    echo "$(kubectl config current-context) at $KUBECONFIG."
    read -r -p "Continue? [y/N] " answer
    [[ $answer == [yY]* ]] || exit 1
fi

step() { printf '\n== %s\n' "$*"; }
uninstall() { local ns=$1; shift; for r in "$@"; do helm uninstall "$r" -n "$ns" --wait 2>/dev/null || true; done; }

step "kagent and Substrate"
# The bridge's Agent first, while kagent's controller can still clean up.
kubectl -n kagent delete agent atomic-bridge --ignore-not-found --wait=true
kubectl -n kagent delete harness atomic-bridge --ignore-not-found --wait=true
kubectl -n kagent delete egresspolicy atomic-serve --ignore-not-found
if helm status kagent -n kagent >/dev/null 2>&1; then
    # Also the runner WorkerPool, which kagent install did not create.
    kagent uninstall --delete-worker-pools
fi
kubectl delete namespace kagent --ignore-not-found

step "agentgateway"
uninstall agentgateway-system agentgateway agentgateway-crds
kubectl delete namespace agentgateway-system --ignore-not-found

step "Istio"
uninstall istio-system ztunnel istio-cni istiod istio-base
kubectl delete namespace istio-system --ignore-not-found

step "The deployment's namespaces"
kubectl delete namespace atomic atomic-runners models --ignore-not-found

step "Down"
echo "Left: k3s, host-setup.sh's changes, the registry container on 127.0.0.1:5000."
