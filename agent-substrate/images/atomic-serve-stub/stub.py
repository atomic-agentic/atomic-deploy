# SPDX-License-Identifier: Apache-2.0
"""A stand-in for `atomic serve`, until atomic has it. Standard library only.

  GET /readyz   200
  POST *        200 with the request it received: headers, the claims of a JWT
                in Authorization (its signature redacted) and the body

Listens on 8080, as a non-root user. The kagent bridge forwards gate
decisions here; check.sh reads them back from this pod's log to see the actor
JWT Substrate's egress gateway put in.
"""

import base64
import http.server
import json


def claims(token):
    pad = lambda s: s + "=" * (-len(s) % 4)
    header, payload, _ = token.split(".")
    return {"header": json.loads(base64.urlsafe_b64decode(pad(header))),
            "payload": json.loads(base64.urlsafe_b64decode(pad(payload)))}


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
        if self.path == "/readyz":
            self.reply(200, {"ok": True})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        headers, jwt = {}, None
        for key, value in self.headers.items():
            if key.lower() == "authorization" and value.count(".") == 2:
                jwt = claims(value.split(" ", 1)[-1])
                value = value.split(".")[0] + ".<claims below>.<signature redacted>"
            headers[key] = value
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = body.decode("utf-8", "replace")
        report = {"method": self.command, "path": self.path, "headers": headers, "jwt": jwt, "body": parsed}
        print("request " + json.dumps(report), flush=True)
        self.reply(200, report)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("", 8080), Handler).serve_forever()
