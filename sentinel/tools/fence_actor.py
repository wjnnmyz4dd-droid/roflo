"""Split-brain actor: one OS process competing for execution authority.

Usage: fence_actor.py <workdir> <owner> <seconds> [pause_label]
Real wall clock (optionally skewed by SENTINEL_CLOCK_SKEW seconds). The broker is the SimBroker with
fencing DISABLED (like MT5: the broker accepts anything), so only Sentinel's lease + fenced send
ledger can prevent a stale writer. ``pause_label`` makes the process SIGSTOP itself at that point
(simulating CPU starvation / VM freeze); the parent resumes it with SIGCONT.
"""

import json
import os
import pathlib
import signal
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.broker.sim import SimBroker  # noqa: E402
from sentinel.contracts import Direction  # noqa: E402
from sentinel.lease import Lease, LeaseLost  # noqa: E402

wd, owner, seconds = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
pause = sys.argv[4] if len(sys.argv) > 4 else ""
skew = float(os.environ.get("SENTINEL_CLOCK_SKEW", "0"))


class Clock:
    def now(self):
        return time.time() + skew


def emit(**kw):
    print(json.dumps({"owner": owner, "t": round(time.time(), 3), **kw}), flush=True)


paused_once = False


def pause_point(label):
    global paused_once
    if pause == label and not paused_once:
        paused_once = True
        emit(event="PAUSED", at=label)
        os.kill(os.getpid(), signal.SIGSTOP)
        emit(event="RESUMED", at=label)


lease = Lease(str(wd / "coord.db"), owner, ttl=2.0, guard=0.5, clock=Clock(), busy_timeout=float(os.environ.get("SENTINEL_DB_TIMEOUT", "30")))
broker = SimBroker(str(wd / "broker.db"), enforce_fencing=False)
broker.set_quote("XAUUSD", 2000.0, 2000.3)
end = time.time() + seconds
n = 0
while time.time() < end:
    try:
        if lease.acquire():
            lease.assert_leader()
            n += 1
            intent = f"{owner}-{n}"
            pause_point("before_record")
            token = lease.record_send(intent)
            pause_point("after_record")
            broker.send_order(intent, "XAUUSD", Direction.LONG, 0.01, 1990.0, None, 20, token)
            emit(event="SENT", intent=intent, token=token)
            if os.environ.get("SENTINEL_ONE_SHOT"):
                break
    except LeaseLost as e:
        emit(event="REFUSED", reason=str(e))
        if os.environ.get("SENTINEL_ONE_SHOT"):
            break
    except Exception as e:  # noqa: BLE001 - e.g. coordination store unreachable (partition)
        emit(event="ERROR", reason=repr(e)[:200])
    time.sleep(0.2)
emit(event="EXIT")
