#!/usr/bin/env bash
# Back up Solvent's durable state.
#
#   ./deploy/backup.sh [destination-directory]
#
# Uses SQLite's online backup API through Python, which takes a consistent
# snapshot of a live WAL database while the service keeps running. Copying the
# file with cp can capture a torn database; this cannot. Python is used rather
# than the sqlite3 CLI because Solvent already requires Python 3.11 and the CLI
# is not installed on every minimal host -- one fewer thing to go missing.
#
# SECRETS ARE DELIBERATELY NOT INCLUDED. /etc/solvent/solvent.env holds the owner
# signing key and the Stripe webhook secret. A backup that quietly contained them
# would turn every copy into somewhere they can leak from. Back that file up
# separately and deliberately, wherever you keep secrets.
set -euo pipefail

DB="${SOLVENT_DB:-/var/lib/solvent/solvent.db}"
DEST="${1:-/var/backups/solvent}"
KEEP="${SOLVENT_BACKUP_KEEP:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/solvent-$STAMP.db"

[[ -f "$DB" ]] || { echo "no database at $DB" >&2; exit 1; }
mkdir -p "$DEST"

# Snapshot, then verify. A backup nobody has checked is a hope, not a backup --
# so this asserts both that SQLite can read it and that Solvent's own hash chain
# still validates inside the copy.
python3 - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(dst_path)
with dst:
    src.backup(dst)
src.close()

if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit("INTEGRITY CHECK FAILED")
dst.close()

sys.path.insert(0, "/opt/solvent")
try:
    from solvent.audit import AuditLog
    from solvent.store import Store
except ImportError:
    print("  integrity: ok (audit chain not checked: not on the service host)")
    sys.exit(0)
store = Store(dst_path)
ok, detail = AuditLog(store).verify_chain()
store.close()
print(f"  integrity: ok | audit chain: {detail}")
sys.exit(0 if ok else "AUDIT CHAIN FAILED in the backup")
PY

gzip -f "$OUT"
echo "  wrote $OUT.gz"

# Retention: keep the newest $KEEP, delete the rest.
ls -1t "$DEST"/solvent-*.db.gz 2>/dev/null | tail -n "+$((KEEP + 1))" \
    | xargs -r rm -f
echo "  keeping the newest $KEEP backup(s) in $DEST"
