#!/bin/sh
# Solvent liveness watchdog.
#
# The point of this script is what it does NOT trust.
#
# Solvent has a health check, and `solvent health` reports it. That check runs
# inside Solvent, which means a Solvent that is wedged, deadlocked, out of
# memory or simply not running reports nothing at all — and "nothing at all"
# looks exactly like "quiet because everything is fine". A process cannot
# notice its own absence.
#
# So this runs from a systemd timer, outside the service, and looks at a
# heartbeat *file* the runtime touches as it works. A stale heartbeat is the one
# symptom Solvent cannot produce while broken, and cannot fake while healthy:
# writing it requires getting far enough through a tick to write it.
#
# No new infrastructure. A timer, a file, and a stat.
#
#   install: deploy/install.sh places this at /usr/local/bin/solvent-watchdog
#   run:     systemd timer, every minute (see deploy/solvent-watchdog.timer)

set -eu

HEARTBEAT="${SOLVENT_HEARTBEAT:-/var/lib/solvent/heartbeat}"
# Three missed minutes rather than one: a single slow tick is not an outage,
# and a watchdog that cries at the first late beat is a watchdog that gets
# muted.
MAX_AGE_SECONDS="${SOLVENT_HEARTBEAT_MAX_AGE:-180}"
UNIT="${SOLVENT_UNIT:-solvent.service}"

fail() {
    echo "solvent-watchdog: $1" >&2
    # Recorded where an operator will find it even if Solvent itself cannot
    # write anything: the journal belongs to the host, not to the service.
    command -v systemd-cat >/dev/null 2>&1 &&
        printf 'solvent-watchdog: %s\n' "$1" | systemd-cat -t solvent-watchdog -p err
    exit 1
}

# 1. Is the unit even meant to be up? A unit that was deliberately stopped is
#    not an outage, and reporting it as one teaches people to ignore this.
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-enabled --quiet "$UNIT" 2>/dev/null; then
        echo "solvent-watchdog: $UNIT is not enabled; nothing to watch"
        exit 0
    fi
    systemctl is-active --quiet "$UNIT" ||
        fail "$UNIT is enabled and not running"
fi

# 2. The heartbeat. This is the check Solvent cannot answer on its own behalf.
[ -e "$HEARTBEAT" ] ||
    fail "no heartbeat at $HEARTBEAT; the runtime has not completed a tick"

NOW=$(date +%s)
BEAT=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || stat -f %m "$HEARTBEAT")
AGE=$((NOW - BEAT))

[ "$AGE" -le "$MAX_AGE_SECONDS" ] ||
    fail "heartbeat is ${AGE}s old (limit ${MAX_AGE_SECONDS}s); the runtime is \
up but not working"

# 3. The website, if it is meant to be listening. Loopback only: this script
#    checks that the socket answers, not that it is reachable from anywhere —
#    reachable from anywhere is the thing the firewall exists to prevent.
if [ -n "${SOLVENT_WEB_PORT:-}" ] && command -v curl >/dev/null 2>&1; then
    curl --silent --show-error --fail --max-time 5 \
         --output /dev/null "http://127.0.0.1:${SOLVENT_WEB_PORT}/login" ||
        fail "the control centre is not answering on 127.0.0.1:${SOLVENT_WEB_PORT}"
fi

echo "solvent-watchdog: alive (heartbeat ${AGE}s old)"
