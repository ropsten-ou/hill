"""A stand-in for `claude` in its fullscreen mode, in palace's test inside
hill-ops: it watches the mouse, its moves too, as `claude` does then, says so,
and waits to be closed."""

import sys
import time

sys.stdout.write("\x1b[?1003h\x1b[?1006hwatching the mouse\r\n")
sys.stdout.flush()
time.sleep(600)
