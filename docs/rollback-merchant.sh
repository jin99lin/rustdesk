#!/bin/bash
# Roll back deploy-merchant.sh.  Usage: source env.sh; TS=<timestamp printed by deploy> bash rollback-merchant.sh
# Restores code + state.json + adoluna.db.  rustdeskapi.db is NOT restored by default (it would drop anything devices
# wrote since the deploy); the only changes migration makes there are address-book rows in managed collections.
# To restore it too: RESTORE_API_DB=1 (stops rustdesk-api briefly).
set -euo pipefail; cd /workspace/adoluna-remote; : "${TS:?set TS}"
R=/opt/rustdesk/pwpush; A=/opt/rustdesk/api-data/rustdeskapi.db
bash rdvm.sh "set -e; sudo -n systemctl stop adoluna-pwpush
 for f in pwpush.py peerui.js peerui.css password.html state.json adoluna.db; do sudo -n cp -p $R/\$f.bak-mr-$TS $R/\$f; done
 for f in merchants.py merch_api.py merchant_migrate.py merchui.js merchui.css; do sudo -n mv $R/\$f $R/\$f.rolledback-$TS; done
 if [ '${RESTORE_API_DB:-0}' = 1 ]; then U=\$(systemctl list-units --type=service --no-legend | awk '/rustdesk-api|rustdeskapi/{print \$1;exit}'); sudo -n systemctl stop \$U; sudo -n cp -p $A.bak-mr-$TS $A; sudo -n rm -f $A-wal $A-shm; sudo -n systemctl start \$U; fi
 sudo -n rm -rf $R/__pycache__; sudo -n systemctl start adoluna-pwpush; sleep 3; systemctl is-active adoluna-pwpush"
