# SPDX-License-Identifier: Apache-2.0
"""Spike 6: a gate through kagent's public A2A gateway. Standard library only.

  kubectl -n kagent port-forward svc/kagent-controller 18183:8083 &
  python3 -I kagent-gate.py http://127.0.0.1:18183 kagent/atomic-bridge-spike

Sends a message (a new session and task), expects the task to stop at
input-required with a HITL tool_approval_request, waits, then answers the gate
as --user and prints what the bridge reports back.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import uuid

HITL = "https://kagent.dev/extensions/hitl/v1"


def rpc(base, agent, method, params, user):
    body = json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}).encode()
    req = urllib.request.Request(f"{base}/agents/{agent}", data=body, method="POST", headers={
        "Content-Type": "application/json", "A2A-Version": "1.0",
        "A2A-Extensions": HITL, "X-User-Id": user})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def task_of(response):
    result = response.get("result", {})
    return result.get("task", result)


def actor_state(atespace, name_prefix):
    out = subprocess.run(["kubectl-ate", "get", "actors", "-a", atespace, "-o", "json"],
                         capture_output=True, text=True).stdout
    try:
        actors = json.loads(out).get("actors", [])
    except json.JSONDecodeError:
        return "unknown"
    return {a["metadata"]["name"]: a.get("status", {}).get("state") for a in actors
            if a["metadata"]["name"].startswith(name_prefix)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("agent")
    ap.add_argument("--user", default="sam@example.com")
    ap.add_argument("--wait", type=int, default=20, help="seconds the gate waits before the answer")
    ap.add_argument("--approve", action=argparse.BooleanOptionalAction, default=True)
    a = ap.parse_args()

    first = rpc(a.base, a.agent, "SendMessage", {"message": {
        "messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": "Handle the new email"}]}}, a.user)
    if "error" in first:
        print(json.dumps(first, indent=1))
        return 1
    task = task_of(first)
    status = task.get("status", {})
    print(f"task {task.get('id')} context {task.get('contextId')}: {status.get('state')}")
    gate = (status.get("message") or {}).get("metadata", {}).get(HITL)
    print(f"gate as kagent stored it: {json.dumps(gate)}")
    print(f"actor right after the pause: {actor_state('kagent', 'session-')}")
    time.sleep(a.wait)
    print(f"actor after {a.wait} s: {actor_state('kagent', 'session-')}")

    decision = {"type": "tool_approval_response",
                "approvals": [{"id": gate["tools"][0]["id"], "approved": a.approve,
                               **({} if a.approve else {"rejection_reason": "not today"})}]}
    t0 = time.monotonic()
    second = rpc(a.base, a.agent, "SendMessage", {"message": {
        "messageId": str(uuid.uuid4()), "role": "ROLE_USER", "taskId": task["id"], "contextId": task["contextId"],
        "parts": [{"text": "approve" if a.approve else "reject"}],
        "metadata": {HITL: decision}, "extensions": [HITL]}}, a.user)
    if "error" in second:
        print(json.dumps(second, indent=1))
        return 1
    done = task_of(second)
    text = " ".join(p.get("text", "") for p in (done.get("status", {}).get("message") or {}).get("parts", []))
    print(f"after the answer ({time.monotonic() - t0:.2f} s, resume included): {done.get('status', {}).get('state')}")
    print(f"bridge says: {text}")
    return 0 if done.get("status", {}).get("state") in ("TASK_STATE_COMPLETED", "completed") else 2


if __name__ == "__main__":
    sys.exit(main())
