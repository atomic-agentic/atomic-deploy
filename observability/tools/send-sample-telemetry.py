#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Send synthetic atomic telemetry to an OTLP/HTTP endpoint.

For checking a deployment's pipeline and dashboards before a real atomic is
exporting: every instrument in observability/metrics.md, a trace per step
attempt in atomic's span shape, and log records linked to those traces. The
data is labelled deployment.environment.name=sample so it is easy to filter
out, and it is invented: no workflow ran.

    python3 -I observability/tools/send-sample-telemetry.py --endpoint http://127.0.0.1:4318 --minutes 3

Uses the standard library only and OTLP/HTTP with JSON bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request

WORKFLOWS = {
    "change-request": ["plan", "implement", "review"],
    "classify-note": ["classify"],
    "order-report": ["fetch", "summarize"],
}
ATOMS = {"plan": "planner", "implement": "implementer", "review": "reviewer", "classify": "classify-note",
         "fetch": "fetch-orders", "summarize": "summarize-orders"}
MODELS = [("openai", "gpt-strong", "strong", 0.000010), ("openai", "gpt-fast", "fast", 0.000002),
          ("local", "qwen-local", "local", 0.0)]
TOOLS = ["shell", "apply_patch", "atomic.graph_read", "http.get_order"]

SECONDS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300]
WAITS = [1, 5, 10, 20, 30, 45, 60, 120, 300, 600, 1800, 3600]
TOKENS = [64, 256, 1024, 4096, 8192, 16384, 32768, 65536]


def now_ns() -> int:
    return time.time_ns()


def kv(attrs: dict) -> list:
    out = []
    for k, v in attrs.items():
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}
        elif isinstance(v, float):
            val = {"doubleValue": v}
        else:
            val = {"stringValue": str(v)}
        out.append({"key": k, "value": val})
    return out


RESOURCE = {"attributes": kv({
    "service.name": "atomic",
    "service.namespace": "atomic",
    "service.version": "0.0.0-sample",
    "service.instance.id": "sample-0",
    "deployment.environment.name": "sample",
})}


class Meter:
    """Cumulative instruments, exported as OTLP JSON."""

    def __init__(self):
        self.start = now_ns()
        self.inst: dict[str, dict] = {}

    def define(self, name, kind, unit, desc, bounds=None):
        self.inst[name] = {"kind": kind, "unit": unit, "desc": desc, "bounds": bounds or [], "series": {}}

    def _series(self, name, attrs):
        key = json.dumps(attrs, sort_keys=True)
        s = self.inst[name]["series"].get(key)
        if s is None:
            b = self.inst[name]["bounds"]
            s = {"attrs": attrs, "v": 0.0, "count": 0, "sum": 0.0, "buckets": [0] * (len(b) + 1), "ex": None}
            self.inst[name]["series"][key] = s
        return s

    def add(self, name, v, attrs):
        self._series(name, attrs)["v"] += v

    def set(self, name, v, attrs):
        self._series(name, attrs)["v"] = v

    def record(self, name, v, attrs, trace=None):
        s = self._series(name, attrs)
        b = self.inst[name]["bounds"]
        s["count"] += 1
        s["sum"] += v
        i = next((j for j, x in enumerate(b) if v <= x), len(b))
        s["buckets"][i] += 1
        if trace:
            s["ex"] = {"timeUnixNano": str(now_ns()), "asDouble": v, "traceId": trace[0], "spanId": trace[1]}

    def export(self):
        t = str(now_ns())
        metrics = []
        for name, i in self.inst.items():
            pts = []
            for s in i["series"].values():
                p = {"attributes": kv(s["attrs"]), "startTimeUnixNano": str(self.start), "timeUnixNano": t}
                if i["kind"] == "histogram":
                    p.update({"count": str(s["count"]), "sum": s["sum"], "explicitBounds": i["bounds"],
                              "bucketCounts": [str(c) for c in s["buckets"]]})
                    if s["ex"]:
                        p["exemplars"] = [s["ex"]]
                else:
                    p["asDouble"] = float(s["v"])
                pts.append(p)
            if not pts:
                continue
            m = {"name": name, "unit": i["unit"], "description": i["desc"]}
            if i["kind"] == "sum":
                m["sum"] = {"dataPoints": pts, "aggregationTemporality": 2, "isMonotonic": True}
            elif i["kind"] == "gauge":
                m["gauge"] = {"dataPoints": pts}
            else:
                m["histogram"] = {"dataPoints": pts, "aggregationTemporality": 2}
            metrics.append(m)
        return {"resourceMetrics": [{"resource": RESOURCE, "scopeMetrics": [
            {"scope": {"name": "atomic.sample", "version": "1"}, "metrics": metrics}]}]}


def define(m: Meter):
    m.define("atomic.workflow.runs", "sum", "{run}", "Runs started, completed, failed, parked and rejected.")
    m.define("atomic.workflow.active", "gauge", "{run}", "Runs running or waiting at a gate.")
    m.define("atomic.step.duration", "histogram", "s", "Active duration of one step attempt.", SECONDS)
    m.define("atomic.step.queue.duration", "histogram", "s", "Ready to started.", SECONDS)
    m.define("atomic.step.attempts", "sum", "{attempt}", "Step attempts by outcome.")
    m.define("atomic.checkpoint.writes", "sum", "{checkpoint}", "Checkpoints committed.")
    m.define("atomic.runtime.recoveries", "sum", "{run}", "Runs recovered after a restart.")
    m.define("atomic.gate.open", "gauge", "{gate}", "Gates waiting for a decision.")
    m.define("atomic.gate.wait.duration", "histogram", "s", "Gate wait, from durable timestamps.", WAITS)
    m.define("atomic.gate.decisions", "sum", "{decision}", "Gate decisions.")
    m.define("atomic.steering.events", "sum", "{event}", "Steers and stops.")
    m.define("atomic.approval.wait.duration", "histogram", "s", "In-turn approval wait.", WAITS)
    m.define("atomic.schema.repairs", "sum", "{repair}", "Schema repair turns.")
    m.define("atomic.validation.failures", "sum", "{failure}", "Rejected outputs.")
    m.define("atomic.eval.pass_rate", "gauge", "1", "Eval pass rate at the last evaluation.")
    m.define("atomic.cost", "sum", "USD", "Priced model-call cost.")
    m.define("atomic.model.throttled", "sum", "{request}", "Throttled requests.")
    m.define("atomic.model.fallbacks", "sum", "{request}", "Fallback requests.")
    m.define("atomic.tool.duration", "histogram", "s", "Tool call duration.", SECONDS)
    m.define("atomic.tool.errors", "sum", "{error}", "Tool call errors.")
    m.define("atomic.graph.items", "gauge", "{item}", "Work-graph items by state.")
    m.define("atomic.graph.claim.contention", "sum", "{claim}", "Lost claims.")
    m.define("atomic.graph.message.cursor.lag", "gauge", "{message}", "Unread messages.")
    m.define("atomic.runner.utilization", "gauge", "1", "Busy runner slots.")
    m.define("codex.turns", "sum", "{turn}", "Agent turns.")
    m.define("gen_ai.client.token.usage", "histogram", "{token}", "Tokens per model call.", TOKENS)
    m.define("gen_ai.client.operation.duration", "histogram", "s", "Model call duration.", SECONDS)
    m.define("process.memory.usage", "gauge", "By", "Process memory.")
    m.define("process.uptime", "gauge", "s", "Process uptime.")


def hexid(n):
    return os.urandom(n).hex()


def span(trace, sid, parent, name, start, end, attrs, error=False, kind=1, links=None):
    s = {"traceId": trace, "spanId": sid, "name": name, "kind": kind,
         "startTimeUnixNano": str(start), "endTimeUnixNano": str(end), "attributes": kv(attrs),
         "status": {"code": 2, "message": "step failed"} if error else {"code": 1}}
    if parent:
        s["parentSpanId"] = parent
    if links:
        s["links"] = links
    return s


def step_attempt(m: Meter, rnd: random.Random, spans: list, logs: list):
    wf = rnd.choice(list(WORKFLOWS))
    step = rnd.choice(WORKFLOWS[wf])
    atom = ATOMS[step]
    run = "run-" + hexid(4)
    trace, root = hexid(16), hexid(8)
    end = now_ns()
    dur = rnd.lognormvariate(1.5, 0.9)
    start = end - int(dur * 1e9)
    replay = rnd.random() < 0.05
    failed = rnd.random() < 0.08
    base = {"atomic.workflow.name": wf, "atomic.workflow.id": run, "atomic.step": step, "atomic.attempt": 1,
            "gen_ai.agent.name": atom, "gen_ai.operation.name": "invoke_agent"}
    if replay:
        base["atomic.replay"] = True
    repairs = 1 if rnd.random() < 0.1 else 0
    if repairs:
        base["atomic.schema.repairs"] = repairs
    spans.append(span(trace, root, None, f"step {step}", start, end, base, error=failed,
                      links=[{"traceId": hexid(16), "spanId": hexid(8), "attributes": kv({"atomic.link": "workflow"})}]))
    provider, model, tier, price = rnd.choice(MODELS)
    for _ in range(1 + repairs):
        cs = start + int(rnd.uniform(0.05, 0.5) * dur * 1e9)
        ce = min(end, cs + int(rnd.uniform(0.2, 0.6) * dur * 1e9))
        tin, tout = rnd.randint(500, 9000), rnd.randint(50, 1500)
        sid = hexid(8)
        ga = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider, "gen_ai.request.model": model}
        spans.append(span(trace, sid, root, f"chat {model}", cs, ce,
                          {**ga, "gen_ai.usage.input_tokens": tin, "gen_ai.usage.output_tokens": tout,
                           "atomic.cost_usd": round((tin + tout) * price, 6), "atomic.workflow.name": wf}, kind=3))
        secs = (ce - cs) / 1e9
        m.record("gen_ai.client.token.usage", tin, {**ga, "gen_ai.token.type": "input"}, (trace, sid))
        m.record("gen_ai.client.token.usage", tout, {**ga, "gen_ai.token.type": "output"})
        m.record("gen_ai.client.operation.duration", secs, ga, (trace, sid))
        m.add("atomic.cost", (tin + tout) * price, {"atomic.model.tier": tier, "gen_ai.provider.name": provider,
                                                   "atomic.project": "sample", "atomic.workflow.name": wf})
        m.add("codex.turns", 1, {"atomic.model.tier": tier})
        if rnd.random() < 0.03:
            m.add("atomic.model.throttled", 1, {"gen_ai.provider.name": provider, "atomic.model.tier": tier})
        if rnd.random() < 0.02:
            m.add("atomic.model.fallbacks", 1, {"atomic.model.tier": tier})
    if repairs:
        m.add("atomic.schema.repairs", 1, {"atomic.atom": atom})
        m.add("atomic.validation.failures", 1, {"atomic.atom": atom})
    for _ in range(rnd.randint(0, 3)):
        tool = rnd.choice(TOOLS)
        ts_ = start + int(rnd.uniform(0.1, 0.8) * dur * 1e9)
        te = min(end, ts_ + int(rnd.uniform(0.05, 2) * 1e9))
        err = rnd.random() < 0.05
        spans.append(span(trace, hexid(8), root, f"execute_tool {tool}", ts_, te,
                          {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool,
                           "atomic.workflow.name": wf}, error=err))
        m.record("atomic.tool.duration", (te - ts_) / 1e9, {"gen_ai.tool.name": tool})
        if err:
            m.add("atomic.tool.errors", 1, {"gen_ai.tool.name": tool, "error.type": "timeout"})
    outcome = "failed" if failed else "completed"
    m.record("atomic.step.duration", dur, {"atomic.workflow.name": wf, "atomic.step": step, "atomic.atom.kind": "codex"},
             (trace, root))
    m.record("atomic.step.queue.duration", rnd.expovariate(2), {"atomic.workflow.name": wf})
    m.add("atomic.step.attempts", 1, {"atomic.atom": atom, "atomic.attempt.outcome": outcome})
    m.add("atomic.checkpoint.writes", 1, {"atomic.workflow.name": wf})
    if replay:
        m.add("atomic.runtime.recoveries", 1, {"atomic.workflow.name": wf})
    logs.append({"timeUnixNano": str(end), "observedTimeUnixNano": str(end),
                 "severityNumber": 17 if failed else 9, "severityText": "ERROR" if failed else "INFO",
                 "body": {"stringValue": f"step {step} {outcome}"}, "traceId": trace, "spanId": root,
                 "attributes": kv({"atomic.workflow.name": wf, "atomic.workflow.id": run, "atomic.step": step})})
    if step in ("plan", "review") and rnd.random() < 0.5:
        decision = rnd.choices(["approved", "rejected", "expired"], [8, 2, 1])[0]
        m.record("atomic.gate.wait.duration", rnd.lognormvariate(4, 1.2), {"atomic.workflow.name": wf, "atomic.gate.decision": decision},
                 (trace, root))
        m.add("atomic.gate.decisions", 1, {"atomic.gate.decision": decision,
                                           "atomic.gate.channel": rnd.choice(["terminal", "http", "slack", "ai-reviewer"])})
    if wf == "change-request" and step == "implement" and rnd.random() < 0.3:
        m.record("atomic.approval.wait.duration", rnd.lognormvariate(2.5, 1), {"atomic.approval.decision": rnd.choice(["allowed", "declined"])})
        m.add("atomic.steering.events", 1, {"atomic.steering.kind": rnd.choice(["steer", "stop"])})
    return wf, outcome


def post(endpoint, path, body):
    req = urllib.request.Request(endpoint.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318"))
    ap.add_argument("--minutes", type=float, default=3.0, help="how long to keep sending (default 3)")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between exports (default 10)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    m = Meter()
    define(m)
    born = time.time()
    deadline = born + args.minutes * 60
    active = {wf: 0 for wf in WORKFLOWS}
    n = 0
    while True:
        spans, logs = [], []
        for _ in range(rnd.randint(3, 8)):
            wf, outcome = step_attempt(m, rnd, spans, logs)
            m.add("atomic.workflow.runs", 1, {"atomic.workflow.name": wf, "atomic.project": "sample", "atomic.run.status": "started"})
            m.add("atomic.workflow.runs", 1, {"atomic.workflow.name": wf, "atomic.project": "sample", "atomic.run.status": outcome})
            if rnd.random() < 0.04:
                m.add("atomic.workflow.runs", 1, {"atomic.workflow.name": wf, "atomic.project": "sample", "atomic.run.status": "parked"})
        for wf in WORKFLOWS:
            active[wf] = max(0, active[wf] + rnd.randint(-1, 2))
            m.set("atomic.workflow.active", active[wf], {"atomic.workflow.name": wf, "atomic.run.state": "running"})
            m.set("atomic.workflow.active", rnd.randint(0, 2), {"atomic.workflow.name": wf, "atomic.run.state": "waiting"})
            m.set("atomic.gate.open", rnd.randint(0, 2) if wf != "classify-note" else 0, {"atomic.workflow.name": wf})
        for atom in ATOMS.values():
            m.set("atomic.eval.pass_rate", round(rnd.uniform(0.75, 1.0), 3), {"atomic.atom": atom})
        for state, hi in (("ready", 12), ("claimed", 6), ("blocked", 9), ("closed", 80)):
            m.set("atomic.graph.items", rnd.randint(hi // 2, hi), {"atomic.graph.state": state})
        if rnd.random() < 0.2:
            m.add("atomic.graph.claim.contention", 1, {"atomic.project": "sample"})
        m.set("atomic.graph.message.cursor.lag", rnd.randint(0, 5), {"atomic.project": "sample"})
        m.set("atomic.runner.utilization", round(rnd.uniform(0.2, 0.9), 3), {"atomic.runner.backend": "local"})
        m.set("process.memory.usage", 180e6 + rnd.uniform(0, 40e6), {"process.memory.type": "rss"})
        m.set("process.uptime", time.time() - born, {})
        try:
            post(args.endpoint, "/v1/metrics", m.export())
            post(args.endpoint, "/v1/traces", {"resourceSpans": [{"resource": RESOURCE, "scopeSpans": [
                {"scope": {"name": "atomic.sample"}, "spans": spans}]}]})
            post(args.endpoint, "/v1/logs", {"resourceLogs": [{"resource": RESOURCE, "scopeLogs": [
                {"scope": {"name": "atomic.sample"}, "logRecords": logs}]}]})
        except Exception as e:  # noqa: BLE001 - report and stop
            print(f"send failed: {e}", file=sys.stderr)
            return 1
        n += 1
        print(f"export {n}: {len(spans)} spans, {len(logs)} log records", flush=True)
        if time.time() + args.interval > deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
