#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run the execution-only spike in a deployed atomic-spikes-exec-only stack:
# start the control-plane task, give the Lambda runner its private address,
# and print both sides' results.
set -euo pipefail
STACK="${STACK:-atomic-spikes-exec-only}"
OUT="${OUT:-$(mktemp -d)}"
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
CLUSTER="$(out Cluster)"

task="$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$(out ControlPlaneTask)" --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$(out ControlPlaneSubnet)],securityGroups=[$(out ControlPlaneSg)],assignPublicIp=ENABLED}" \
    --query 'tasks[0].taskArn' --output text)"
echo "control plane task ${task##*/}"
aws ecs wait tasks-running --cluster "$CLUSTER" --tasks "$task"
ip="$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task" \
    --query "tasks[0].attachments[0].details[?name=='privateIPv4Address'].value" --output text)"
echo "control plane at ${ip}; invoking the runner"

start=$(date +%s)
aws lambda invoke --function-name "$(out Runner)" --cli-read-timeout 660 --cli-binary-format raw-in-base64-out \
    --payload "{\"rendezvous\": \"${ip}:8765\", \"environment_id\": \"spike\", \"deadline_s\": 480}" \
    "$OUT/runner.json" >/dev/null
echo "runner returned after $(( $(date +%s) - start )) s"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task"
# CloudWatch can lag the task's end by a few seconds.
for _ in 1 2 3 4 5 6 7 8 9 10; do
    aws logs get-log-events --log-group-name /atomic-spikes/exec-only/control-plane \
        --log-stream-name "cp/control-plane/${task##*/}" --query 'events[].message' --output text 2>/dev/null \
        | tr '\t' '\n' > "$OUT/control-plane.log"
    grep -q 'RENDEZVOUS' "$OUT/control-plane.log" && break
    sleep 3
done
echo "results in $OUT"
