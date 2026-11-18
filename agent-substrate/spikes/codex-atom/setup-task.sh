#!/bin/sh
# Creates the atom's task repository at $1 (default /work/task): a function
# with a bug and a test that catches it. Run where the commands will run (the
# actor, in both placements).
set -eu
d=${1:-/work/task}
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
