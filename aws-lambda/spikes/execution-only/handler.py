# SPDX-License-Identifier: Apache-2.0
#
# The runner side of execution-only placement on Lambda: Codex's exec-server
# (atomic's fork, which makes the parent-death signal best effort) in its
# `direct` remote mode, dialling out to the control plane's rendezvous. The
# function accepts no connections and holds no model credentials; its role may
# only write its logs.
#
# exec-server requires TLS for a non-loopback registry, so it talks to a
# loopback relay in this handler, which forwards the bytes to the rendezvous
# across the VPC, as atomic runner's loopback shim would.
import json
import os
import socket
import subprocess
import threading
import time
import urllib.request

RELAY_PORT = 8090
TASK = "/tmp/work/task"
# The task from the Agent Substrate spikes (agent-substrate/spikes/codex-atom),
# created where the commands run.
SETUP = '''
set -eu
d=%s
rm -rf "$d" && mkdir -p "$d" && cd "$d"
cat > calc.py <<'PY'
def add(a, b):
    """Return the sum of a and b."""
    return a - b
PY
cat > test_calc.py <<'PY'
from calc import add

assert add(2, 3) == 5, f"add(2, 3) returned {add(2, 3)}"
assert add(-1, 1) == 0
print("ok")
PY
git init -q -b main && git -c user.email=spike@example.com -c user.name=spike add -A \
  && git -c user.email=spike@example.com -c user.name=spike commit -q -m "calc with a bug"
python3 test_calc.py || true
''' % TASK


# Lambda reuses the execution environment, threads and listening sockets
# included (a second invocation failed with "Address already in use" before
# this): the relay starts once per environment, and each invocation only
# points it at its own control plane.
TARGET = {"addr": None}
RELAY_STARTED = []


def relay(target):
    TARGET["addr"] = target
    if RELAY_STARTED:
        return
    RELAY_STARTED.append(True)
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", RELAY_PORT))
    server.listen(16)

    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def serve():
        while True:
            client, _ = server.accept()
            host, port = TARGET["addr"].rsplit(":", 1)
            upstream = socket.create_connection((host, int(port)), timeout=10)
            upstream.settimeout(None)
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()


def status(target):
    try:
        return json.loads(urllib.request.urlopen("http://%s/status" % target, timeout=5).read())
    except Exception as e:
        return {"error": str(e)}


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=60)
    return (p.stdout + p.stderr).strip()


def handler(event, context):
    target = event["rendezvous"]
    env_id = event.get("environment_id", "spike")
    deadline = time.time() + event.get("deadline_s", 420)
    started = time.time()
    warm = bool(RELAY_STARTED)
    setup = run(SETUP)
    relay(target)
    t_relay = time.time()
    env = dict(os.environ, HOME="/tmp", CODEX_HOME="/tmp/codex")
    os.makedirs("/tmp/codex", exist_ok=True)
    log = open("/tmp/exec-server.log", "w")
    server = subprocess.Popen(
        ["/opt/codex/codex", "exec-server", "--remote", "http://127.0.0.1:%d" % RELAY_PORT,
         "--environment-id", env_id, "--remote-transport", "direct", "--aws-sigv4",
         "--aws-region", os.environ["AWS_REGION"]],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    last = {}
    connected_after_s = None
    while time.time() < deadline:
        last = status(target)
        if connected_after_s is None and last.get("executor_connected_at_s") is not None:
            connected_after_s = round(time.time() - t_relay, 1)
        if last.get("done") or server.poll() is not None:
            break
        time.sleep(0.5)
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    log.close()
    return {
        "seconds": round(time.time() - started, 1),
        "warm_environment": warm,
        "setup_s": round(t_relay - started, 1),
        "exec_server_connected_after_s": connected_after_s,
        "exec_server_exit": server.returncode,
        "setup": setup[-300:],
        "rendezvous": last,
        "git_diff": run("git diff", cwd=TASK)[-800:],
        "test_now": run("python3 test_calc.py", cwd=TASK)[-300:],
        "exec_server_log": open("/tmp/exec-server.log").read()[-1500:],
    }
