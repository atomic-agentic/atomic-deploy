# SPDX-License-Identifier: Apache-2.0
"""A probe actor for the Substrate spikes. Standard library only.

  GET /readyz           200, the actor's wake-up probe
  GET /sandbox          spike 5: what Codex's sandbox would find in this actor
  GET /codex            spike 5: Codex's own sandbox command, when Codex is in the image
  GET /ws               spike 4: a WebSocket echo (RFC 6455, no extensions)
  GET /egress?url=URL   what an outbound request from the actor gets

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
        else:
            self.reply(404, {"error": "not found"})

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
    http.server.ThreadingHTTPServer(("", 80), Handler).serve_forever()
