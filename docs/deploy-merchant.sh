#!/bin/bash
# Deploy the merchant admin redesign (wt-merch, branch merchant-redesign).
# /opt/rustdesk/pwpush is root 700: never cd into it; sudo -n + absolute paths.  Usage: source env.sh; bash deploy-merchant.sh
set -euo pipefail; cd /workspace/adoluna-remote
TS=${TS:-$(date +%Y%m%d-%H%M%S)}; D=wt-merch/res/adoluna/server/pwpush; R=/opt/rustdesk/pwpush; A=/opt/rustdesk/api-data/rustdeskapi.db
OLD="pwpush.py peerui.js peerui.css password.html state.json adoluna.db"
NEW="merchants.py merch_api.py merchant_migrate.py merchui.js merchui.css"
# 1. timestamped backups (sqlite copies use the online backup API, safe while rustdesk-api is running)
bash rdvm.sh "set -e; for f in $OLD; do sudo -n cp -p $R/\$f $R/\$f.bak-mr-$TS; done
 sudo -n python3 -c 'import sqlite3,sys;s=sqlite3.connect(sys.argv[1]);d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close()' $A $A.bak-mr-$TS
 sudo -n chmod 600 $A.bak-mr-$TS; sudo -n ls -l $R/ /opt/rustdesk/api-data/ | grep bak-mr-$TS"
# 2. upload + compile check
for f in pwpush.py peerui.js peerui.css password.html $NEW; do bash rdvm.sh "cat > /tmp/mr.$f" < $D/$f; done
bash rdvm.sh "set -e; cd /tmp; for f in pwpush.py merchants.py merch_api.py merchant_migrate.py; do python3 -m py_compile mr.\$f; done; rm -rf /tmp/__pycache__"
# 3. stop sidecar, install, migrate (dry run printed first), start
bash rdvm.sh "set -e; sudo -n systemctl stop adoluna-pwpush
 for f in pwpush.py peerui.js peerui.css password.html $NEW; do sudo -n install -m 644 -o root -g root /tmp/mr.\$f $R/\$f; done; rm -f /tmp/mr.*
 echo '--- dry run'; sudo -n env PW_DIR=$R PW_DB=$A python3 $R/merchant_migrate.py
 echo '--- apply';   sudo -n env PW_DIR=$R PW_DB=$A python3 $R/merchant_migrate.py --apply
 sudo -n chmod 600 $R/state.json $R/adoluna.db; sudo -n rm -rf $R/__pycache__
 sudo -n systemctl start adoluna-pwpush; sleep 3; systemctl is-active adoluna-pwpush"
echo "TS=$TS"
