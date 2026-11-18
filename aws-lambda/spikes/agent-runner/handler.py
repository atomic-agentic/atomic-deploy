# SPDX-License-Identifier: Apache-2.0
#
# One Codex turn per invocation, as the design's `agent` runner would run one
# atom attempt. Codex's own sandbox cannot start in Lambda (spike 1), so it
# runs with its sandbox off and the execution environment is the boundary,
# which is what gap 7 asks atomic to select by policy.
import os
import shutil
import subprocess
import time

import shim

REGION = os.environ["AWS_REGION"]
CODEX_HOME = "/tmp/codex"
WORK = "/tmp/work"
CONFIG = """\
model_provider = "amazon-bedrock"
model = "openai.gpt-oss-120b"
web_search = "disabled"
check_for_update_on_startup = false

[analytics]
enabled = false

[feedback]
enabled = false

[history]
persistence = "none"

[features]
multi_agent = false

[model_providers.amazon-bedrock]
base_url = "http://127.0.0.1:8080/v1"
aws = { region = "%s" }
""" % REGION

INIT_STARTED = time.time()
shim.serve(REGION)
INIT = {"init_s": None, "invocations": 0}


def probe():
    """Which process-setup calls Lambda's seccomp policy refuses, each tried in
    a child just before exec, where Codex makes them."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    def attempt(name, fn):
        def pre():
            if fn() != 0:
                os._exit(100 + ctypes.get_errno())
        try:
            p = subprocess.run(["/bin/true"], preexec_fn=pre, capture_output=True, timeout=10)
            code = p.returncode
            return "ok" if code == 0 else ("errno %d (%s)" % (code - 100, os.strerror(code - 100)) if code >= 100 else "exit %d" % code)
        except Exception as e:
            return "%s: %s" % (type(e).__name__, e)
    results = {
        "prctl(PR_SET_NO_NEW_PRIVS)": attempt("nnp", lambda: libc.prctl(38, 1, 0, 0, 0)),
        "prctl(PR_SET_PDEATHSIG)": attempt("pdeath", lambda: libc.prctl(1, 9, 0, 0, 0)),
        "prctl(PR_SET_DUMPABLE, 0)": attempt("dumpable", lambda: libc.prctl(4, 0, 0, 0, 0)),
        "setsid()": attempt("setsid", lambda: 0 if libc.setsid() >= 0 else -1),
        "setpgid(0, 0)": attempt("setpgid", lambda: libc.setpgid(0, 0)),
        "setrlimit(RLIMIT_CORE, 0)": attempt("rlimit", lambda: libc.setrlimit(4, (ctypes.c_ulong * 2)(0, 0))),
    }
    try:
        m, s = os.openpty(); os.close(m); os.close(s); results["openpty()"] = "ok"
    except OSError as e:
        results["openpty()"] = "errno %d (%s)" % (e.errno, e.strerror)
    results["/dev/ptmx exists"] = os.path.exists("/dev/ptmx")
    results["/dev/pts"] = sorted(os.listdir("/dev/pts")) if os.path.isdir("/dev/pts") else "missing"
    return results


def handler(event, context):
    if event.get("probe"):
        return probe()
    INIT["invocations"] += 1
    if INIT["init_s"] is None:
        INIT["init_s"] = round(time.time() - INIT_STARTED, 2)
    # Attempt hygiene (gap 8): /tmp survives between invocations and even a
    # runtime crash, so each attempt starts from an empty workspace and home.
    for path in (CODEX_HOME, WORK):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
    # Extra [features] flags per invocation, for example
    # {"unified_exec_tty": false}: Lambda has no pseudo-terminals.
    features = "".join("%s = %s\n" % (k, "true" if v else "false") for k, v in event.get("features", {}).items())
    with open(os.path.join(CODEX_HOME, "config.toml"), "w") as f:
        f.write(CONFIG.replace("multi_agent = false\n", "multi_agent = false\n" + features))
    shim.STATS.clear()
    env = dict(os.environ, CODEX_HOME=CODEX_HOME, HOME="/tmp")
    started = time.time()
    p = subprocess.run(
        ["/opt/codex/codex", "exec", "--skip-git-repo-check", "--sandbox", "danger-full-access", event["prompt"]],
        cwd=WORK, env=env, capture_output=True, text=True, timeout=270,
    )
    return {
        "invocation_in_environment": INIT["invocations"],
        "seconds_since_init": round(time.time() - INIT_STARTED, 2),
        "codex_seconds": round(time.time() - started, 2),
        "exit": p.returncode,
        "stdout": p.stdout[-2500:],
        "stderr": p.stderr[-2500:],
        "model_requests": list(shim.STATS),
    }
