# SPDX-License-Identifier: Apache-2.0
"""A probe actor for the Substrate spikes. Standard library only.

  GET /readyz           200, the actor's wake-up probe
  GET /sandbox          spike 5: what Codex's sandbox would find in this actor
  GET /codex            spike 5: Codex's own sandbox command, when Codex is in the image
  GET /ws               spike 4: a WebSocket echo (RFC 6455, no extensions)
  GET /headers, POST *  reports the request: headers, JWT claims (signature redacted), body;
                        run as a stand-in for atomic serve in spike 6
  GET /slow?s=N         answers after N seconds (the router's request timeout)
  GET /stream?s=N       streams a line every 5 s for N seconds (a model turn's shape)
  GET /egress?url=URL   what an outbound request from the actor gets
  GET /quiescence       spike 12: whether the actor is safe to suspend (200) or not (409),
                        as atomic runner would report it; see quiescence() below

Listens on port 80, the port Substrate's router sends to by default.
"""

import base64
import ctypes
import hashlib
import http.server
import json
import os
import platform
import socket
import struct
import subprocess
import urllib.parse
import urllib.request

BWRAP = "/opt/probe/bwrap"  # Codex rust-v0.169.0's bwrap, verified at build time
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError as e:
        return f"unreadable: {e}"


def proc_status():
    keep = ("Seccomp", "Seccomp_filters", "NoNewPrivs", "CapEff", "CapBnd", "Uid")
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            key, _, value = line.partition(":")
            if key in keep:
                out[key] = value.strip()
    return out


def unshare_user():
    pid = os.fork()
    if pid == 0:
        try:
            os.unshare(os.CLONE_NEWUSER)
            os._exit(0)
        except OSError as e:
            os._exit(e.errno or 1)
    code = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    return "ok" if code == 0 else f"errno {code} ({os.strerror(code)})"


def landlock():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)
    r = libc.syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    e = ctypes.get_errno()
    return f"abi {r}" if r >= 0 else f"errno {e} ({os.strerror(e)})"


def seccomp_filter():
    """Install an allow-all seccomp filter in a child, as Codex does in-process."""
    pid = os.fork()
    if pid == 0:
        libc = ctypes.CDLL(None, use_errno=True)

        class SockFilter(ctypes.Structure):
            _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                        ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

        class SockFprog(ctypes.Structure):
            _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(SockFilter))]

        prog_filter = (SockFilter * 1)(SockFilter(0x06, 0, 0, 0x7FFF0000))  # BPF_RET|BPF_K, SECCOMP_RET_ALLOW
        prog = SockFprog(1, prog_filter)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            os._exit(ctypes.get_errno() or 1)
        if libc.prctl(22, 2, ctypes.byref(prog), 0, 0) != 0:  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
            os._exit(ctypes.get_errno() or 1)
        os._exit(0)
    code = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    return "ok" if code == 0 else f"errno {code} ({os.strerror(code)})"


def bwrap():
    # Codex rust-v0.169.0 (codex-rs/linux-sandbox/src/bwrap.rs) always passes
    # --unshare-user --unshare-ipc, usually --unshare-pid with --proc, and
    # --unshare-net unless the network mode is FullAccess.
    codex = ["--unshare-user", "--unshare-ipc", "--unshare-pid", "--proc", "/proc",
             "--bind", "/tmp", "/tmp"]
    cases = {
        "none": [],
        "user": ["--unshare-user"],
        "user+net": ["--unshare-user", "--unshare-net"],
        "user+pid+proc": ["--unshare-user", "--unshare-pid", "--proc", "/proc"],
        "codex-full-network": codex,
        "codex-isolated-network": codex + ["--unshare-net"],
    }
    out = {}
    for name, extra in cases.items():
        argv = [BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", *extra, "--", "/bin/true"]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        out[name] = "ok" if p.returncode == 0 else f"exit {p.returncode}: {p.stderr.strip()[:200]}"
    return out


def codex():
    """Codex's own sandbox command under each mode atomic might choose.

    "inside" writes in the workspace (/work), which workspace-write allows;
    "outside" writes to /etc, which every mode but danger-full-access must block.
    """
    if not os.path.exists("/usr/local/bin/codex"):
        return {"error": "no codex in this image"}
    # Codex 0.169's built-in permission profiles; :workspace takes its network
    # setting from sandbox_workspace_write.network_access.
    modes = {
        "workspace, network off": (":workspace", ["sandbox_workspace_write.network_access=false"]),
        "workspace, network on": (":workspace", ["sandbox_workspace_write.network_access=true"]),
        # A custom profile: :workspace's filesystem rules with the network
        # policy enabled, so Codex leaves bwrap's network namespace alone and
        # Substrate's egress policy does the network isolation.
        "workspace, network enabled by profile": (
            "actor", ['permissions.actor.extends=":workspace"', "permissions.actor.network.enabled=true"]),
        "read-only": (":read-only", []),
        "danger-full-access": (":danger-full-access", []),
    }
    writes = {"inside": "echo ok > /work/probe && cat /work/probe",
              "outside": "echo x > /etc/codex-probe && echo wrote-outside"}
    out = {}
    for mode, (profile, config) in modes.items():
        for where, script in writes.items():
            argv = ["codex", "sandbox", "-P", profile]
            for c in config:
                argv += ["-c", c]
            argv += ["-C", "/work", "--", "sh", "-c", script]
            p = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd="/work")
            out[f"{mode} / {where}"] = {
                "exit": p.returncode,
                "stdout": p.stdout.strip()[-200:],
                "stderr": p.stderr.strip()[-300:],
            }
            if os.path.exists("/etc/codex-probe"):
                os.remove("/etc/codex-probe")
    return out


def sandbox():
    return {
        "kernel": platform.release(),
        "version": read("/proc/version"),
        "uid": os.getuid(),
        "status": proc_status(),
        "max_user_namespaces": read("/proc/sys/user/max_user_namespaces"),
        "lsm": read("/sys/kernel/security/lsm"),
        "unshare_user": unshare_user(),
        "landlock": landlock(),
        "seccomp_filter": seccomp_filter(),
        "bwrap": bwrap(),
    }


def egress(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    out = {"url": url}
    try:
        out["dns"] = sorted({a[4][0] for a in socket.getaddrinfo(host, None)})
    except OSError as e:
        out["dns"] = f"error: {e}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            out["status"] = r.status
            out["body"] = r.read(200).decode("utf-8", "replace")
    except Exception as e:  # report every failure as data
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# --- Quiescence (spike 12) --------------------------------------------------
#
# Substrate has no idle signal of its own, and Codex's events alone are not
# proof (spike 11: after a volumes-only suspend they still showed a turn in
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

RUNNER_COMMANDS = ("codex-exec-server", "/opt/probe/server.py")
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
    # was lost (spike 11, volumes fidelity). Not safe to resume as if busy, and
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


def ws_recv(rfile):
    head = rfile.read(2)
    if len(head) < 2:
        return None, None
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b"\0\0\0\0"
    data = bytearray(rfile.read(length))
    for i in range(len(data)):
        data[i] ^= mask[i % 4]
    return opcode, bytes(data)


def ws_send(wfile, opcode, data):
    head = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    wfile.write(head + data)
    wfile.flush()


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
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/readyz":
            self.reply(200, {"ok": True})
        elif url.path == "/sandbox":
            self.reply(200, sandbox())
        elif url.path == "/codex":
            self.reply(200, codex())
        elif url.path == "/egress":
            target = urllib.parse.parse_qs(url.query).get("url", [""])[0]
            self.reply(200, egress(target))
        elif url.path == "/ws":
            self.websocket()
        elif url.path == "/quiescence":
            r = quiescence_report()
            self.reply(200 if r["quiescent"] else 409, r)
        elif url.path == "/headers":
            self.reply(200, self.request_report())
        elif url.path in ("/slow", "/stream"):
            self.long_response(url)
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        """Any POST: report the request, as a stand-in for atomic serve."""
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.reply(200, self.request_report(body))

    def request_report(self, body=b""):
        headers = {}
        claims = None
        for key, value in self.headers.items():
            if key.lower() == "authorization" and value.count(".") == 2:
                # A JWT: show its claims, never the signature.
                token = value.split(" ", 1)[-1]
                header_b64, payload_b64, _ = token.split(".")
                pad = lambda s: s + "=" * (-len(s) % 4)
                claims = {"header": json.loads(base64.urlsafe_b64decode(pad(header_b64))),
                          "payload": json.loads(base64.urlsafe_b64decode(pad(payload_b64)))}
                value = value.split(".")[0] + ".<claims below>.<signature redacted>"
            headers[key] = value
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = body.decode("utf-8", "replace")
        return {"method": self.command, "path": self.path, "client": self.client_address[0],
                "headers": headers, "jwt": claims, "body": parsed}

    def long_response(self, url):
        """/slow?s=N answers after N seconds; /stream?s=N streams a line every 5 s."""
        import time
        seconds = int(urllib.parse.parse_qs(url.query).get("s", ["90"])[0])
        if url.path == "/slow":
            time.sleep(seconds)
            self.reply(200, {"slept": seconds})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        start = time.monotonic()
        while (elapsed := time.monotonic() - start) < seconds:
            line = f"t={elapsed:.0f}s\n".encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
            self.wfile.flush()
            time.sleep(5)
        self.wfile.write(b"0\r\n\r\n")
        self.close_connection = True

    def websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if "websocket" not in self.headers.get("Upgrade", "").lower() or not key:
            self.reply(400, {"error": "not a WebSocket upgrade",
                             "upgrade": self.headers.get("Upgrade"),
                             "connection": self.headers.get("Connection")})
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        while True:
            opcode, data = ws_recv(self.rfile)
            if opcode is None or opcode == 0x8:
                if opcode == 0x8:
                    ws_send(self.wfile, 0x8, data[:2])
                break
            if opcode == 0x9:
                ws_send(self.wfile, 0xA, data)
            elif opcode in (0x1, 0x2):
                ws_send(self.wfile, opcode, data)
        self.close_connection = True

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    import threading
    threading.Thread(target=_sampler, daemon=True).start()
    http.server.ThreadingHTTPServer(("", 80), Handler).serve_forever()
