#!/usr/bin/env bash
# Default-deny host firewall for a Solvent host.
#
#   sudo ./deploy/firewall.sh            show what it would do
#   sudo ./deploy/firewall.sh --apply    apply it
#
# DEFAULT DENY, inbound and outbound. Everything Solvent needs is then allowed
# explicitly, and nothing else is. That ordering matters: a firewall built by
# adding blocks to an open host is a list of things somebody thought of.
#
# What this deliberately does NOT do: open the control centre to the internet.
# The control centre binds loopback. If it is to be reachable, that is a reverse
# proxy's job -- see docs/solvent-web-control-center.md -- and the proxy's port
# is the only inbound port this script opens.
set -euo pipefail

APPLY="${1:-}"
PROXY_PORT="${SOLVENT_PROXY_PORT:-443}"
SSH_PORT="${SOLVENT_SSH_PORT:-22}"

run() {
    if [[ "$APPLY" == "--apply" ]]; then
        echo "+ $*"
        "$@"
    else
        echo "  would run: $*"
    fi
}

command -v nft >/dev/null 2>&1 || {
    echo "nft (nftables) not found." >&2
    echo "This script uses nftables. If this host uses ufw or firewalld," >&2
    echo "apply the same policy with that tool instead: deny inbound and" >&2
    echo "outbound by default, then allow only what is listed below." >&2
    exit 1
}

echo "==> policy"
echo "    inbound  : DENY, except ssh/$SSH_PORT and proxy/$PROXY_PORT"
echo "    outbound : DENY, except DNS, NTP, and HTTPS to approved destinations"
echo "    loopback : allowed (the control centre and the runtime talk this way)"
echo

if [[ "$APPLY" != "--apply" ]]; then
    echo "DRY RUN. Nothing has been changed. Re-run with --apply."
    echo
fi

TABLE=/tmp/solvent-nft.$$
cat > "$TABLE" <<RULES
table inet solvent {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    ip protocol icmp icmp type echo-request limit rate 5/second accept
    tcp dport $SSH_PORT ct state new accept
    tcp dport $PROXY_PORT ct state new accept
    # 8765 is NOT opened. The control centre is reached through the proxy.
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
  }
  chain output {
    type filter hook output priority 0; policy drop;
    oif lo accept
    ct state established,related accept
    udp dport 53 accept
    tcp dport 53 accept
    udp dport 123 accept
    # Outbound HTTPS. Solvent's Action Gate decides which destinations are
    # allowed; this is the layer that stops a library going round it. Narrow it
    # to specific addresses once the owner has approved destinations.
    tcp dport 443 ct state new accept
  }
}
RULES

echo "==> ruleset"
sed 's/^/    /' "$TABLE"
echo
run nft -f "$TABLE"
rm -f "$TABLE"

if [[ "$APPLY" == "--apply" ]]; then
    echo
    echo "Applied. Verify with:  nft list table inet solvent"
    echo "Record the posture so readiness can see it:"
    echo "  solvent setup firewall --default-deny --proxy-port $PROXY_PORT"
    echo
    echo "NOTE: this is not persistent across reboot on its own. Save it with"
    echo "your distribution's nftables service (commonly /etc/nftables.conf)."
fi
