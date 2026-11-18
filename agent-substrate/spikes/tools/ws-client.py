# SPDX-License-Identifier: Apache-2.0
"""A minimal WebSocket client for spike 4. Standard library only.

  python3 -I ws-client.py ws://127.0.0.1:8000/ws
  python3 -I ws-client.py wss://host:443/ws --header 'ate-target-actor: a/b' \
      --cert client.pem --ca ca.pem --server-name host

Prints the handshake status and each echoed message; exits 0 only if the
upgrade succeeded and every message came back unchanged.
"""

import argparse
import base64
import os
import socket
import ssl
import struct
import sys
import urllib.parse


def send_frame(sock, opcode, data):
    mask = os.urandom(4)
    head = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def recv_exact(f, n):
    data = f.read(n)
    if len(data) < n:
        raise EOFError("connection closed")
    return data


def recv_frame(f):
    head = recv_exact(f, 2)
    opcode, length = head[0] & 0x0F, head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(f, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(f, 8))[0]
    return opcode, recv_exact(f, length)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--cert", help="client certificate and key, PEM")
    ap.add_argument("--ca", help="CA bundle for the server, PEM")
    ap.add_argument("--server-name")
    ap.add_argument("--message", action="append", default=["hello", "x" * 70000])
    a = ap.parse_args()

    u = urllib.parse.urlsplit(a.url)
    tls = u.scheme == "wss"
    port = u.port or (443 if tls else 80)
    sock = socket.create_connection((u.hostname, port), timeout=15)
    if tls:
        ctx = ssl.create_default_context(cafile=a.ca)
        if a.cert:
            ctx.load_cert_chain(a.cert)
        sock = ctx.wrap_socket(sock, server_hostname=a.server_name or u.hostname)
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [f"GET {u.path or '/'} HTTP/1.1", f"Host: {u.netloc}", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
             *a.header]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    f = sock.makefile("rb")
    status = f.readline().decode().strip()
    while f.readline() not in (b"\r\n", b""):
        pass
    print(f"handshake: {status}")
    if " 101 " not in status + " ":
        print(f.read(500).decode("utf-8", "replace"))
        return 1
    ok = True
    for m in a.message:
        send_frame(sock, 0x1, m.encode())
        op, data = recv_frame(f)
        same = data == m.encode()
        ok &= same
        print(f"echo: {len(m)} bytes sent, {len(data)} received, {'same' if same else 'DIFFERENT'}")
    send_frame(sock, 0x8, struct.pack("!H", 1000))
    print(f"close: opcode {recv_frame(f)[0]}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
