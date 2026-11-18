# SPDX-License-Identifier: Apache-2.0
"""A minimal Codex exec-server client for spike 4. Standard library only.

  python3 -I exec-client.py ws://127.0.0.1:18130/ [--argv sh -c 'echo hi']

Follows exec-server's documented lifecycle (codex-rs/exec-server/README.md at
rust-v0.169.0): initialize, initialized, process/start, then process/read until
the process has exited. Prints each exchange; exits 0 when the command ran.
"""

import argparse
import base64
import json
import os
import socket
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ws = __import__("ws-client")


class Session:
    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.netloc}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.f = self.sock.makefile("rb")
        status = self.f.readline().decode().strip()
        while self.f.readline() not in (b"\r\n", b""):
            pass
        print(f"handshake: {status}")
        if " 101 " not in status + " ":
            raise SystemExit(1)
        self.next_id = 1

    def send(self, msg):
        ws.send_frame(self.sock, 0x1, json.dumps(msg).encode())

    def call(self, method, params):
        rid = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            op, data = ws.recv_frame(self.f)
            if op != 0x1:
                continue
            msg = json.loads(data)
            if msg.get("id") == rid:
                return msg
            print(f"  notification: {json.dumps(msg)[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--argv", nargs=argparse.REMAINDER,
                    default=["sh", "-c", "echo hello from $(uname -r); id -u"])
    a = ap.parse_args()

    s = Session(a.url)
    r = s.call("initialize", {"clientName": "atomic-spike"})
    print(f"initialize: {json.dumps(r)[:300]}")
    if "error" in r:
        return 1
    s.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    t0 = time.monotonic()
    r = s.call("process/start", {"processId": "p1", "argv": a.argv, "cwd": "file:///work",
                                  "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
                                  "tty": False, "pipeStdin": False, "arg0": None})
    print(f"process/start: {json.dumps(r)[:300]}")
    if "error" in r:
        return 1
    after, out = None, b""
    while True:
        r = s.call("process/read", {"processId": "p1", "afterSeq": after, "maxBytes": 65536, "waitMs": 2000})
        res = r.get("result", {})
        for c in res.get("chunks", []):
            chunk = c.get("chunk") if isinstance(c, dict) else None
            out += base64.b64decode(chunk) if isinstance(chunk, str) else json.dumps(c).encode()
        after = res.get("nextSeq", after)
        if "error" in r or res.get("exited") or res.get("closed"):
            break
    print(f"process/read: exited={res.get('exited')} exitCode={res.get('exitCode')} "
          f"failure={res.get('failure')} after {time.monotonic() - t0:.2f} s")
    print(f"output: {out.decode('utf-8', 'replace').strip()}")
    ws.send_frame(s.sock, 0x8, b"\x03\xe8")
    return 0 if res.get("exitCode") == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
