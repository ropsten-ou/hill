"""A stand-in for `claude` in palace's tests: it writes what it was started
with to $FAKE_CLAUDE_LOG, as JSON (its arguments, its folder, and palace's
variables in its environment), then exits."""

import json
import os
import sys

with open(os.environ["FAKE_CLAUDE_LOG"], "w") as log:
    json.dump({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": {k: v for k, v in os.environ.items() if k in ("PALACE_CLAUDE_KEY", "PALACE_SELECT", "PALACE_STATE_HOME")},
    }, log)
