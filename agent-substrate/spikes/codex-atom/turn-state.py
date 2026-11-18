# Prints what an observer inside the actor can see about the Codex turn:
# Codex's event stream, the processes alive, and the turn's outputs.
import json, os, time

print("at", time.strftime("%H:%M:%S", time.gmtime()))
events = []
try:
    events = [json.loads(l) for l in open("/work/run/events.jsonl") if l.strip()]
except OSError:
    pass
started = {e["item"]["id"]: e["item"] for e in events if e.get("type") == "item.started"}
done = {e["item"]["id"] for e in events if e.get("type") == "item.completed"}
types = [e.get("type") for e in events]
print("events:", len(events), "| turn completed:", "turn.completed" in types,
      "| turn failed:", "turn.failed" in types,
      "| items in progress:", [i.get("type") for k, i in started.items() if k not in done])
for e in events[-3:]:
    print("  last:", json.dumps(e)[:160])
procs = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode().strip()
    except OSError:
        continue
    if any(w in cmd for w in ("codex exec", "slow.sh", "sleep 60")) and "turn-state" not in cmd:
        procs.append(f"{pid}:{cmd[:60]}")
print("processes:", procs or "none")
for f in ("/work/task/marker", "/work/run/codex-exit", "/work/run/last-message.txt"):
    try:
        print(f"{f}:", open(f).read().strip()[:200] or "(empty)")
    except OSError:
        print(f"{f}: absent")
print("codex state db:", [n for n in os.listdir("/work/codex-home") if n.endswith(".sqlite")] if os.path.isdir("/work/codex-home") else "none")
