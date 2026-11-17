#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run every query in atomic's dashboards through a live Grafana.

Each panel query is sent to Grafana's query API with the dashboard variables
set to "all", and the result is reported as ok, empty or an error. Use it after
a deployment comes up, with real or sample telemetry flowing
(send-sample-telemetry.py), to check that datasources, metric names and SQL
match what the backends hold.

    python3 -I observability/tools/check-dashboards.py --url http://127.0.0.1:3000 --user admin --password ...

Exits 1 if any query errors. Empty results are reported but do not fail,
unless --strict is given: a metric atomic has not emitted yet is empty, not
wrong.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DASHBOARDS = Path(__file__).resolve().parent.parent / "grafana" / "dashboards"

ALL = {"env": ".*", "workflow": ".*", "database": ".*"}
RAW = {"durable_schema": "dbos"}


def substitute(text: str) -> str:
    for name, value in RAW.items():
        text = text.replace("${%s:raw}" % name, value).replace("$" + name, value)
    for name, value in ALL.items():
        text = text.replace("${%s}" % name, value).replace("$" + name, value)
    return text


def api(base, auth, path, body=None):
    req = urllib.request.Request(base.rstrip("/") + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {"message": str(e)}
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"cannot reach Grafana at {base}: {getattr(e, 'reason', e)}")


def query_for(target):
    t = dict(target)
    for key in ("expr", "rawSql", "query"):
        if key in t and isinstance(t[key], str):
            t[key] = substitute(t[key])
    t.setdefault("intervalMs", 15000)
    t.setdefault("maxDataPoints", 400)
    return t


def rows_in(result):
    n = 0
    for frame in result.get("frames", []):
        values = frame.get("data", {}).get("values", [])
        if values:
            n += max((len(v) for v in values), default=0)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000"))
    ap.add_argument("--user", default=os.environ.get("GRAFANA_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("GRAFANA_PASSWORD"))
    ap.add_argument("--from", dest="since", default="now-1h")
    ap.add_argument("--strict", action="store_true", help="also fail on empty results")
    args = ap.parse_args()
    if not args.password:
        print("set --password or GRAFANA_PASSWORD", file=sys.stderr)
        return 2
    auth = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()

    errors = empty = ok = 0
    for path in sorted(DASHBOARDS.glob("*.json")):
        dash = json.loads(path.read_text())
        status, live = api(args.url, auth, f"/api/dashboards/uid/{dash['uid']}")
        state = "provisioned" if status == 200 else f"NOT LOADED ({status})"
        print(f"\n{dash['title']}  [{state}]")
        if status != 200:
            errors += 1
        for panel in dash["panels"]:
            for target in panel.get("targets", []):
                ds = target.get("datasource") or panel.get("datasource")
                if ds.get("type") == "tempo" and target.get("queryType") == "traceql":
                    # Grafana runs TraceQL searches from the browser, not
                    # through its query API, so ask Tempo through the proxy.
                    q = urllib.parse.quote(substitute(target["query"]))
                    status, res = api(args.url, auth, f"/api/datasources/proxy/uid/{ds['uid']}/api/search?q={q}&limit=5")
                    label = f"  {panel['title']} [{target['refId']}]"
                    if status >= 400:
                        errors += 1
                        print(f"{label}: ERROR {res.get('message', status)}")
                    elif not res.get("traces"):
                        empty += 1
                        print(f"{label}: empty")
                    else:
                        ok += 1
                    continue
                body = {"queries": [{**query_for(target), "datasource": ds}], "from": args.since, "to": "now"}
                status, res = api(args.url, auth, "/api/ds/query", body)
                result = res.get("results", {}).get(target["refId"], {})
                err = result.get("error") or (res.get("message") if status >= 400 else None)
                label = f"  {panel['title']} [{target['refId']}]"
                if err:
                    errors += 1
                    print(f"{label}: ERROR {err}")
                elif rows_in(result) == 0:
                    empty += 1
                    print(f"{label}: empty")
                else:
                    ok += 1
        time.sleep(0.05)
    print(f"\n{ok} ok, {empty} empty, {errors} errors")
    if errors or (args.strict and empty):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
