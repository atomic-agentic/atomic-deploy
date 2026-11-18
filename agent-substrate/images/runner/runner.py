# SPDX-License-Identifier: Apache-2.0
"""The runner's HTTP side, a stand-in for `atomic runner`. Standard library only.

  GET /readyz       200, the actor's wake-up probe
  GET /quiescence   whether the actor is safe to suspend (200) or not (409), and
                    why; see quiescence() below

Listens on port 80, the port Substrate's router sends to by default. Codex's
exec-server runs beside it on port 8080. atomic's own implementation of the
check is `atomic_exec::quiescence`; this file goes when `atomic runner` serves
the same report.
"""

import http.server
import json
import os
import subprocess
import urllib.parse


# --- Quiescence ---------------------------------------------------------------
#
# Substrate has no idle signal of its own, and Codex's events alone are not
# proof (findings.md, spike 11: after a volumes-only suspend they still showed a turn in
# progress that no longer existed). The runner therefore reports quiescence as
# the conjunction of everything that can still be in flight, and atomic
# suspends only on it. Configuration, as atomic runner would know it per
# attempt:
#
#   QUIESCENCE_EVENTS     Codex's JSON event log for the attempt (codex exec --json)
#   QUIESCENCE_WORKTREE   the attempt's git worktree
#   QUIESCENCE_DURABLE    the actor's durable directory (where CODEX_HOME must be)
#   CODEX_HOME            Codex's state directory
#   QUIESCENCE_EXEC_PORT  exec-server's port; an open session means a control
#                         plane is driving the actor

RUNNER_COMMANDS = ("codex-exec-server", "/opt/atomic/runner.py")
_OWN_GIT = "git -c quiescence.check=1 "  # marks the runner's own git calls in /proc
_quiet_since = None  # monotonic time quiescence was first seen, by the sampler


def _codex_turn(path):
    """The attempt's turn as Codex's event log tells it."""
    try:
        events = [json.loads(line) for line in open(path) if line.strip()]
    except OSError:
        return {"log": "absent", "open": False, "open_items": []}
    except ValueError as e:
        return {"log": f"unreadable: {e}", "open": True, "open_items": []}
    started, done, turn_open = {}, set(), False
    for e in events:
        t = e.get("type")
        if t == "turn.started":
            turn_open = True
        elif t in ("turn.completed", "turn.failed"):
            turn_open = False
        elif t == "item.started":
            started[e["item"]["id"]] = e["item"].get("type")
        elif t == "item.completed":
            done.add(e["item"]["id"])
    return {"log": "present", "open": turn_open,
            "open_items": [v for k, v in started.items() if k not in done],
            "last": events[-1].get("type") if events else None}


def _attempt_processes():
    """Every live process that is not the runner's own: not PID 1, not this
    server, not a known runner program, and not the server's own git call.
    Not filtered by parent: an attempt's orphaned background processes are
    reparented to PID 1, which may be the runner itself."""
    out = []
    me = os.getpid()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        if int(pid) in (1, me):
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode().strip()
            state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if not cmd or state == "Z" or any(r in cmd for r in RUNNER_COMMANDS) or cmd.startswith(_OWN_GIT):
            continue
        out.append({"pid": int(pid), "cmd": cmd[:80]})
    return out


def _exec_sessions(port):
    """Established connections to exec-server, from /proc/net/tcp{,6}."""
    n = 0
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = open(f).read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            if len(cols) > 3 and cols[3] == "01" and int(cols[1].rsplit(":", 1)[1], 16) == port:
                n += 1
    return n


def _worktree(path):
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"clean": True, "note": "no worktree"}
    try:
        status = subprocess.run(["git", "-c", "quiescence.check=1", "-C", path, "status", "--porcelain", "--branch"],
                                capture_output=True, text=True, timeout=20).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"clean": False, "note": f"git failed: {e}"}
    head, changes = (status[0] if status else ""), status[1:]
    return {"clean": not changes and "[ahead" not in head,
            "uncommitted": len(changes), "unpushed": "[ahead" in head}


def quiescence():
    env = os.environ
    turn = _codex_turn(env.get("QUIESCENCE_EVENTS", "/work/run/events.jsonl"))
    procs = _attempt_processes()
    sessions = _exec_sessions(int(env.get("QUIESCENCE_EXEC_PORT", "8080")))
    tree = _worktree(env.get("QUIESCENCE_WORKTREE", "/work/task"))
    durable = env.get("QUIESCENCE_DURABLE", "/work").rstrip("/") + "/"
    codex_home = env.get("CODEX_HOME", "")
    codex_alive = any("codex" in p["cmd"].split(" ", 1)[0] for p in procs)

    reasons = []
    if turn["open"] or turn["open_items"]:
        reasons.append(f"Codex turn open ({', '.join(turn['open_items']) or 'no item'} in progress)")
    if procs:
        reasons.append(f"{len(procs)} attempt process(es) alive")
    if sessions:
        reasons.append(f"{sessions} exec-server session(s) open")
    if not tree["clean"]:
        reasons.append("worktree has uncommitted or unpushed work")
    if codex_home and not codex_home.startswith(durable):
        reasons.append(f"CODEX_HOME ({codex_home}) is not on the durable directory")
    # Codex's log says a turn is running but no Codex process exists: the turn
    # was lost (findings.md, spike 11, volumes fidelity). Not safe to resume as if busy, and
    # not quiescent either: atomic fails or resumes the attempt.
    lost = (turn["open"] or bool(turn["open_items"])) and not codex_alive
    state = "lost" if lost else ("busy" if reasons else "quiescent")
    return {"state": state, "quiescent": state == "quiescent", "reasons": reasons,
            "turn": turn, "processes": procs, "exec_sessions": sessions, "worktree": tree,
            "codex_home": codex_home or None}


def _sampler():
    """Track how long the actor has been quiescent, for an idle timer that
    counts from quiescence. Time inside a suspended actor stops, so this is
    actor time, not wall time."""
    global _quiet_since
    import time
    while True:
        try:
            q = quiescence()["quiescent"]
        except Exception:  # a sampling error is not quiescence
            q = False
        if not q:
            _quiet_since = None
        elif _quiet_since is None:
            _quiet_since = time.monotonic()
        time.sleep(2)


def quiescence_report():
    import time
    r = quiescence()
    since = _quiet_since if r["quiescent"] else None
    r["quiescent_for_s"] = round(time.monotonic() - since, 1) if since is not None else 0.0
    return r


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, body):
        data = json.dumps(body, indent=1).encode() + b"\n"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/readyz":
            self.reply(200, {"ok": True})
        elif path == "/quiescence":
            r = quiescence_report()
            self.reply(200 if r["quiescent"] else 409, r)
        else:
            self.reply(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    import threading
    threading.Thread(target=_sampler, daemon=True).start()
    http.server.ThreadingHTTPServer(("", 80), Handler).serve_forever()
