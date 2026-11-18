#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Bring up the Agent Substrate deployment on the host's k3s, as you (not root),
# after host-setup.sh. Safe to run again: every step converges, and a second
# run changes only what differs.
#
#   ./up.sh            # reads .env beside it, if present
#   ./check.sh         # then: a Codex atom in both placements, a quiescent
#                      # suspend, and a gate through kagent
#
# In order, so strict mTLS is in place before any actor traffic:
#   1. the local registry, and the images built and pushed to it
#   2. kagent 1.0.1 and Substrate 1.0.0 (kagent's charts), with values/
#   3. Istio 1.32 ambient
#   4. agentgateway 1.7
#   5. namespaces, mesh enrollment and policies
#   6. the model gateway and the atomic serve stand-in
#   7. the runner worker pool, atespace and ActorTemplate
#   8. the kagent bridge
set -euo pipefail

KAGENT_VERSION=1.0.1
KAGENT_COMMIT=558e3d7          # kagent v1.0.1, the bridge's SDK
ISTIO_VERSION=1.32.0
ISTIO_CHARTS=https://blob.istio.io/istio-release/charts
AGENTGATEWAY_VERSION=v1.7.0
AGENTGATEWAY_CHARTS=oci://cr.agentgateway.dev/charts
SUBSTRATE_CHARTS=oci://ghcr.io/kagent-dev/substrate/helm
SUBSTRATE_VERSION=1.0.0
# registry:3
REGISTRY_IMAGE=registry@sha256:82f6ff4a7f982960c8aa51617fea3aabc485df6e55cce80d22deb1de36f1bc3c
REGISTRY=localhost:5000
DOCKER0=172.17.0.1

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
export MODEL_HOST_IP=${MODEL_HOST_IP:-$DOCKER0}
export MODEL_PORT=${MODEL_PORT:-13305}
export MODEL_NAME=${MODEL_NAME:-Qwen3.6-35B-A3B-MTP-GGUF-UD-Q4_K_XL}
export ATESPACE=${ATESPACE:-atomic}
export WORKER_REPLICAS=${WORKER_REPLICAS:-2}
MODEL_PROVIDER_KEY=${MODEL_PROVIDER_KEY:-unused}
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/k3s.yaml}
cache=${XDG_CACHE_HOME:-$HOME/.cache}/atomic-deploy

step() { printf '\n== %s\n' "$*"; }
die() { echo "up.sh: $*" >&2; exit 1; }
# kubectl-ate warns on every call that it acts as the shared ate-client identity.
ate() { kubectl-ate "$@" 2> >(grep -v '^Warning: no --token-file' >&2); }
# envsubst with only the named variables, so nothing else in a file changes.
render() { local f=$1; shift; envsubst "$(printf '${%s} ' "$@")" < "$f"; }

step "Prerequisites"
for c in kubectl helm kagent kubectl-ate docker git envsubst python3 curl; do
    command -v "$c" >/dev/null || die "missing command: $c"
done
python3 -I -c 'import yaml' 2>/dev/null || die "python3 needs PyYAML (python3-pyyaml)"
v=$(kagent version 2>/dev/null | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["kagent_version"])')
[[ $v == "$KAGENT_VERSION" ]] || die "kagent CLI is $v; this deployment pins $KAGENT_VERSION"
kubectl get nodes >/dev/null || die "no cluster at KUBECONFIG=$KUBECONFIG; run sudo ./host-setup.sh first"
kv=$(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}')
[[ $kv == v1.37.* ]] || die "k3s is $kv; host-setup.sh installs v1.37"
echo "k3s $kv, kagent CLI $v"

step "1. Registry and images"
if ! curl -fsS -m 3 -o /dev/null "http://127.0.0.1:5000/v2/"; then
    docker run -d --name atomic-registry --restart=unless-stopped \
        -p 127.0.0.1:5000:5000 -p "$DOCKER0:5000:5000" "$REGISTRY_IMAGE" >/dev/null
    for _ in $(seq 20); do curl -fsS -m 3 -o /dev/null "http://127.0.0.1:5000/v2/" && break; sleep 1; done
fi
curl -fsS -m 3 -o /dev/null "http://$DOCKER0:5000/v2/" || die "the registry does not answer on $DOCKER0:5000, where atelet pulls from"

mkdir -p "$cache"
kagent_src=$cache/kagent-v$KAGENT_VERSION
if [[ ! -d $kagent_src/.git ]]; then
    git clone -q --depth 1 --branch "v$KAGENT_VERSION" https://github.com/kagent-dev/kagent.git "$kagent_src"
fi
[[ $(git -C "$kagent_src" rev-parse --short=7 HEAD) == "$KAGENT_COMMIT" ]] \
    || die "$kagent_src is not kagent v$KAGENT_VERSION ($KAGENT_COMMIT)"

# Build, push, and print the image by digest, as the registry reports it. A
# fixed SOURCE_DATE_EPOCH and no provenance attestation make an unchanged
# build the same image, so a second run of up.sh replaces nothing.
push() {
    local name=$1; shift
    SOURCE_DATE_EPOCH=0 docker buildx build --load -q --provenance=false \
        -t "$REGISTRY/$name:latest" "$@" >/dev/null
    docker push -q "$REGISTRY/$name:latest" >/dev/null
    local digest
    digest=$(curl -fsS -I -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
        "http://127.0.0.1:5000/v2/$name/manifests/latest" | tr -d '\r' | awk 'tolower($1)=="docker-content-digest:" {print $2}')
    [[ $digest == sha256:* ]] || die "no digest for $name"
    echo "$REGISTRY/$name@$digest"
}
export RUNNER_IMAGE STUB_IMAGE BRIDGE_IMAGE
RUNNER_IMAGE=$(push atomic-runner images/runner)
STUB_IMAGE=$(push atomic-serve-stub images/atomic-serve-stub)
BRIDGE_IMAGE=$(push atomic-kagent-bridge --build-context "kagent=$kagent_src/go" images/kagent-bridge)
printf '%s\n' "$RUNNER_IMAGE" "$STUB_IMAGE" "$BRIDGE_IMAGE"

step "2. kagent $KAGENT_VERSION and Substrate $SUBSTRATE_VERSION"
# True when every value in the file is already set on the release, so a
# second run does not upgrade Substrate (and restart its pods) for nothing.
values_applied() {
    helm get values "$1" -n ate-system -o json | python3 -I -c '
import json, sys, yaml
def subset(want, have):
    if isinstance(want, dict):
        return isinstance(have, dict) and all(subset(v, have.get(k)) for k, v in want.items())
    return want == have
sys.exit(0 if subset(yaml.safe_load(open(sys.argv[1])), json.load(sys.stdin)) else 1)' "$2"
}
if ! helm status kagent -n kagent >/dev/null 2>&1; then
    # kagent install refuses to run without its default provider's key. It goes
    # into kagent's default ModelConfig, which the bridge, a bring-your-own
    # harness, never uses.
    (cd values && OPENAI_API_KEY=${OPENAI_API_KEY:-unused} kagent --config kagent.yaml install --profile minimal)
else
    echo "kagent is installed; checking Substrate's values"
    for r in "substrate values/substrate.yaml" "substrate-atelet values/atelet.yaml"; do
        set -- $r
        if values_applied "$1" "$2"; then
            echo "$1: values in place"
        else
            helm upgrade "$1" "$SUBSTRATE_CHARTS/$1" --version "$SUBSTRATE_VERSION" -n ate-system \
                --reuse-values -f "$2" --wait --timeout 5m >/dev/null
            echo "$1: upgraded with $2"
        fi
    done
fi
kubectl apply -f manifests/atenet-client.yaml

step "3. Istio $ISTIO_VERSION ambient"
helm upgrade -i istio-base base --repo "$ISTIO_CHARTS" --version "$ISTIO_VERSION" \
    -n istio-system --create-namespace --wait >/dev/null
helm upgrade -i istiod istiod --repo "$ISTIO_CHARTS" --version "$ISTIO_VERSION" \
    -n istio-system -f values/istiod.yaml --wait --timeout 5m >/dev/null
helm upgrade -i istio-cni cni --repo "$ISTIO_CHARTS" --version "$ISTIO_VERSION" \
    -n istio-system -f values/istio-cni.yaml --wait --timeout 5m >/dev/null
helm upgrade -i ztunnel ztunnel --repo "$ISTIO_CHARTS" --version "$ISTIO_VERSION" \
    -n istio-system --wait --timeout 5m >/dev/null
helm list -n istio-system --short

step "4. agentgateway $AGENTGATEWAY_VERSION"
helm upgrade -i agentgateway-crds "$AGENTGATEWAY_CHARTS/agentgateway-crds" --version "$AGENTGATEWAY_VERSION" \
    -n agentgateway-system --create-namespace --wait >/dev/null
helm upgrade -i agentgateway "$AGENTGATEWAY_CHARTS/agentgateway" --version "$AGENTGATEWAY_VERSION" \
    -n agentgateway-system --wait --timeout 5m >/dev/null
helm list -n agentgateway-system --short

step "5. Namespaces, mesh enrollment and policies"
kubectl apply -f manifests/namespaces.yaml
kubectl label namespace agentgateway-system istio.io/dataplane-mode=ambient --overwrite
kubectl apply -f manifests/ambient.yaml
label=$(kubectl -n ate-system get deployment atenet-egress \
    -o jsonpath='{.spec.template.metadata.labels.istio\.io/dataplane-mode}')
if [[ $label != ambient ]]; then
    kubectl -n ate-system patch deployment atenet-egress --type merge -p \
        '{"spec":{"template":{"metadata":{"labels":{"istio.io/dataplane-mode":"ambient"}}}}}'
fi
kubectl -n ate-system rollout status deployment atenet-egress --timeout=180s
# Pods that started before their namespace was enrolled stay out of the mesh
# until restarted.
if kubectl -n agentgateway-system get pods \
    -o jsonpath='{range .items[*]}{.metadata.annotations.ambient\.istio\.io/redirection}{"\n"}{end}' \
    | grep -qv '^enabled$'; then
    kubectl -n agentgateway-system rollout restart deployment
    kubectl -n agentgateway-system rollout status deployment --timeout=180s
fi

step "6. Model gateway and the atomic serve stand-in"
render manifests/models.yaml MODEL_PORT MODEL_HOST_IP | kubectl apply -f -
kubectl -n agentgateway-system create secret generic lemonade-key \
    --from-literal=Authorization="$MODEL_PROVIDER_KEY" --dry-run=client -o yaml | kubectl apply -f -
# The IdP's signing keys, through the API server's service proxy (no client
# in the cluster needed). Re-run up.sh after a key rotation.
export IDP_JWKS
IDP_JWKS=$(kubectl get --raw '/api/v1/namespaces/ate-system/services/https:idp:443/proxy/openid/v1/jwks')
python3 -I -c 'import json,sys; k=json.loads(sys.argv[1])["keys"]; assert k and "'"'"'" not in sys.argv[1]' "$IDP_JWKS" \
    || die "unexpected JWKS from Substrate's IdP: $IDP_JWKS"
render manifests/ai-gateway.yaml MODEL_NAME MODEL_PORT ATESPACE IDP_JWKS | kubectl apply -f -
kubectl -n agentgateway-system wait --for=condition=Programmed gateway/ai-gateway --timeout=180s
render manifests/atomic-serve-stub.yaml STUB_IMAGE | kubectl apply -f -
kubectl -n atomic rollout status deployment atomic-serve --timeout=180s

step "7. Runner worker pool, atespace and ActorTemplate"
render manifests/workers.yaml WORKER_REPLICAS | kubectl apply -f -
for _ in $(seq 60); do
    ready=$(kubectl -n atomic-runners get pods -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' | grep -c True || true)
    [[ $ready -ge $WORKER_REPLICAS ]] && break; sleep 5
done
[[ $ready -ge $WORKER_REPLICAS ]] || die "only $ready of $WORKER_REPLICAS runner workers are ready"
echo "$ready runner workers ready"
ate get atespace "$ATESPACE" >/dev/null 2>&1 || ate create atespace "$ATESPACE" >/dev/null
template=$(mktemp); trap 'rm -f "$template"' EXIT
render actors/runner-template.yaml ATESPACE RUNNER_IMAGE > "$template"
current=$(ate get actor-template runner -a "$ATESPACE" -o json 2>/dev/null \
    | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["containers"][0]["image"])' 2>/dev/null || true)
if [[ $current == "$RUNNER_IMAGE" ]]; then
    echo "template runner: current"
else
    if [[ -n $current ]]; then
        ate delete actor-template runner -a "$ATESPACE" >/dev/null \
            || die "template runner has actors; delete them to replace its image"
        echo "template runner: replacing $current"
    fi
    ate create actor-template --preview -f "$template" >/dev/null
fi
for _ in $(seq 60); do
    golden=$(ate get actor-template runner -a "$ATESPACE" -o json 2>/dev/null \
        | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["status"]["goldenSnapshotStatus"]["goldenTag"]["name"])' 2>/dev/null || true)
    [[ -n $golden ]] && break; sleep 3
done
# Substrate leaves a template's status empty while its golden build cannot be
# placed (upstream-issues.md, 1), so a timeout here is the only signal.
[[ -n $golden ]] || die "template runner has no golden snapshot after 3 minutes; are the runner workers full?"
echo "template runner: golden snapshot $golden"

step "8. kagent bridge"
render manifests/kagent-bridge.yaml BRIDGE_IMAGE | kubectl apply -f -

step "Up"
cat <<EOF
Runner template   $ATESPACE/runner  ($RUNNER_IMAGE)
Model gateway     ai-gateway.agentgateway-system -> $MODEL_HOST_IP:$MODEL_PORT ($MODEL_NAME)
kagent agent      kagent/atomic-bridge
atomic serve      the stand-in in namespace atomic
Next: ./check.sh
EOF
