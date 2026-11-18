#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run the spikes in a deployed atomic-spikes stack and print their raw
# results as JSON lines.
#
#   aws cloudformation deploy --stack-name atomic-spikes \
#     --template-file template.yaml --capabilities CAPABILITY_IAM
#   ./run.sh > results.jsonl
#   aws cloudformation delete-stack --stack-name atomic-spikes
set -euo pipefail

STACK="${STACK:-atomic-spikes}"
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
invoke() { # function, payload
    local f; f="$(mktemp)"
    aws lambda invoke --function-name "$1" --cli-binary-format raw-in-base64-out \
        --payload "$2" "$f" --query '{status:StatusCode,error:FunctionError}' --output json >/dev/null
    cat "$f"; rm -f "$f"
}
line() { printf '{"spike":"%s","case":"%s","result":%s}\n' "$1" "$2" "$3"; }

# Spike 1 on Lambda.
line 1 lambda "$(invoke "$(out SandboxProbe)" '{}')"

# Spike 2: plant, check, crash, check.
f="$(out EnvReuse)"
line 2 plant "$(invoke "$f" '{"mode":"plant"}')"
line 2 check-warm "$(invoke "$f" '{"mode":"check"}')"
line 2 crash "$(invoke "$f" '{"mode":"crash"}' || echo '"crashed"')"
line 2 check-after-crash "$(invoke "$f" '{"mode":"check"}')"

# Spike 8: OTLP to CloudWatch from the isolated subnet.
line 8 otlp "$(invoke "$(out OtlpProbe)" '{}')"

# Spike 6: Bedrock under three roles.
default_payload='{"list":true,"mantle":["openai.gpt-oss-120b","openai.gpt-oss-20b","openai.gpt-5.6-luna"],"runtime":["openai.gpt-oss-120b-1:0","openai.gpt-oss-20b-1:0","global.openai.gpt-5.6-luna"]}'
payload="${BEDROCK_PAYLOAD:-$default_payload}"
for fn in BedrockNone BedrockScoped BedrockOpen; do
    line 6 "$fn" "$(invoke "$(out "$fn")" "$payload")"
done

# Spike 1 on Fargate: the same probe source, taken from the deployed function.
src="$(mktemp -d)"
curl -fsSL "$(aws lambda get-function --function-name "$(out SandboxProbe)" --query Code.Location --output text)" -o "$src/code.zip"
probe="$(python3 -I -c 'import sys,zipfile; print(zipfile.ZipFile(sys.argv[1]).read("index.py").decode())' "$src/code.zip")"
rm -rf "$src"
overrides="$(python3 -I -c 'import json,sys; print(json.dumps({"containerOverrides":[{"name":"probe","environment":[{"name":"PROBE","value":sys.argv[1]}]}]}))' "$probe")"
task="$(aws ecs run-task --cluster "$(out Cluster)" --task-definition "$(out SandboxTask)" --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$(out PublicSubnet)],securityGroups=[$(out TaskSg)],assignPublicIp=ENABLED}" \
    --overrides "$overrides" --query 'tasks[0].taskArn' --output text)"
aws ecs wait tasks-stopped --cluster "$(out Cluster)" --tasks "$task"
id="${task##*/}"
result="$(aws logs get-log-events --log-group-name /atomic-spikes/fargate --log-stream-name "probe/probe/$id" \
    --query 'events[-1].message' --output text)"
line 1 fargate "$result"
