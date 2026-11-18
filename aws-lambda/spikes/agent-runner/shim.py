# SPDX-License-Identifier: Apache-2.0
#
# The loopback shim the design puts in `atomic runner`, reduced to what the
# spike needs: Codex's provider base URL points here; each request is
# rewritten for Mantle's /v1 route, re-signed with SigV4 and streamed back.
# In the design it forwards to the control plane's gateway instead of calling
# Bedrock, and the runner's role has no Bedrock permission.
import http.server
import json
import threading
import time
import urllib.error
import urllib.request

import botocore.session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

UPSTREAM = "https://bedrock-mantle.%s.api.aws"
STATS = []
_session = botocore.session.get_session()


def rewrite(body):
    """Mantle's /v1 route rejects an assistant message whose content is a list
    of parts, the standard Responses shape Codex sends back; it accepts the
    same text as a plain string."""
    d = json.loads(body)
    n = 0
    for item in d["input"] if isinstance(d.get("input"), list) else []:
        if item.get("type") == "message" and item.get("role") == "assistant" and isinstance(item.get("content"), list):
            item["content"] = "".join(c.get("text", "") for c in item["content"] if c.get("type") == "output_text")
            for k in ("type", "id", "status"):
                item.pop(k, None)
            n += 1
    return json.dumps(d).encode(), n


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    region = "us-west-2"

    def do_POST(self):
        started = time.time()
        body, n = rewrite(self.rfile.read(int(self.headers["Content-Length"])))
        url = (UPSTREAM % self.region) + self.path
        req = AWSRequest(method="POST", url=url, data=body,
                         headers={"Content-Type": "application/json", "Accept": self.headers.get("Accept", "*/*")})
        SigV4Auth(_session.get_credentials().get_frozen_credentials(), "bedrock-mantle", self.region).add_auth(req)
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, data=body, headers=dict(req.headers), method="POST"), timeout=270)
        except urllib.error.HTTPError as e:
            resp = e
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
        self.end_headers()
        first_byte = None
        while True:
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            if first_byte is None:
                first_byte = time.time() - started
            self.wfile.write(chunk)
            self.wfile.flush()
        STATS.append({"path": self.path, "rewrote": n, "status": resp.status,
                      "first_byte_s": round(first_byte or 0, 2), "total_s": round(time.time() - started, 2)})

    def log_message(self, *args):
        pass


def serve(region):
    Handler.region = region
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
