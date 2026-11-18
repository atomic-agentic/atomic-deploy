# SPDX-License-Identifier: Apache-2.0
#
# Runs the agent-runner spike's shim (../agent-runner/shim.py) as a process of
# its own in the control-plane task: in execution-only placement the model
# calls leave from the control plane, signed with its task role.
import sys
import threading

import shim

shim.serve(sys.argv[1] if len(sys.argv) > 1 else "us-west-2")
threading.Event().wait()
