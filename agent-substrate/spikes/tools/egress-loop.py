# SPDX-License-Identifier: Apache-2.0
"""Run inside an actor: POST to a URL on a schedule and print each outcome.

  python3 -I egress-loop.py URL 'every:15x12,pause:100,every:15x3'

Each request is a new connection from the actor to Substrate's egress gateway;
what the gateway does with its upstream connection is what the experiment
watches. Lines: seconds since start, then the HTTP status or the error.
"""
import sys, time, urllib.error, urllib.request

url, plan = sys.argv[1], sys.argv[2]
t0 = time.monotonic()

def once():
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST"), timeout=20) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:
        return f"{e.code} {e.read()[:60]!r}"
    except Exception as e:
        return f"{type(e).__name__}"

for step in plan.split(","):
    kind, _, arg = step.partition(":")
    if kind == "pause":
        time.sleep(float(arg))
        continue
    every, _, count = arg.partition("x")
    for _ in range(int(count)):
        print(f"t={time.monotonic() - t0:6.1f}s {once()}", flush=True)
        time.sleep(float(every))
