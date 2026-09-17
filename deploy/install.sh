#!/usr/bin/env bash
# Install Solvent as an always-on systemd service.
#
# Idempotent: safe to re-run. It creates nothing it cannot also leave alone, and
# it never overwrites an existing secrets file or database.
#
#   sudo ./deploy/install.sh
#
# It does NOT start real client work. simulation_only stays on, the egress
# allowlist stays empty, and the kill switch stays wherever the owner left it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/solvent
DATA=/var/lib/solvent
CONF=/etc/solvent
LOGS=/var/log/solvent
SERVICE_USER=solvent

[[ $EUID -eq 0 ]] || { echo "run as root: sudo $0" >&2; exit 1; }

echo "==> service account"
id -u "$SERVICE_USER" &>/dev/null || useradd --system --home-dir "$DATA" \
    --shell /usr/sbin/nologin "$SERVICE_USER"

echo "==> directories"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA" "$LOGS"
install -d -o root -g "$SERVICE_USER" -m 0750 "$CONF"
install -d -o root -g root -m 0755 "$PREFIX"

echo "==> application"
# Copy rather than symlink the checkout: an upgrade should be a deliberate act,
# not a side effect of someone running git pull in a development directory.
rsync -a --delete \
      --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      --exclude '.venv' \
      "$REPO/solvent" "$REPO/roflo" "$REPO/roflo.toml" "$REPO/docs" "$PREFIX/"

echo "==> virtualenv"
# Solvent itself has no third-party dependencies; the venv exists to pin an
# interpreter and to keep roflo's optional extras out of the system Python.
[[ -d "$PREFIX/.venv" ]] || python3 -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/python" -c "import sys; assert sys.version_info >= (3,11), sys.version"

echo "==> secrets template"
if [[ ! -f "$CONF/solvent.env" ]]; then
    install -o root -g "$SERVICE_USER" -m 0640 \
        "$REPO/deploy/solvent.env.example" "$CONF/solvent.env"
    echo "    created $CONF/solvent.env - FILL IT IN, it is empty on purpose"
else
    echo "    $CONF/solvent.env exists; left untouched"
fi

echo "==> unit"
install -o root -g root -m 0644 "$REPO/deploy/solvent.service" \
        /etc/systemd/system/solvent.service
systemctl daemon-reload

echo "==> startup check (does not start the service)"
sudo -u "$SERVICE_USER" "$PREFIX/.venv/bin/python" -m solvent.cli health \
     --db "$DATA/solvent.db" --always-on || true

cat <<'NEXT'

Installed. Nothing is running and nothing is enabled yet.

  systemctl enable --now solvent     start it, and start it on every boot
  systemctl status solvent           is the process alive?
  solvent health                     is Solvent able to do anything?
  journalctl -u solvent -f           what is it doing?

Those first two questions are different, and the second is the one that matters.
NEXT
