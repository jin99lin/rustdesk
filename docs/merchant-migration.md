# Merchant redesign: migration plan

1. Backup (deploy-merchant.sh, suffix `.bak-mr-<TS>`): pwpush.py, peerui.js, peerui.css, password.html, state.json, adoluna.db, rustdeskapi.db (sqlite online backup).
2. Stop adoluna-pwpush, install files, `merchant_migrate.py` dry run, then `--apply`:
   - each device group becomes a store; "X - Y" → merchant X / store Y; other names → merchant <name> / store "总店 Main"; groups 2 and 34 → merchant "Adoluna 内部 / Internal". The store keeps the same device group id, so no device moves.
   - legacy grants (user group / user → device) become positions with access to the stores holding those devices; members = users currently in those user groups (non-admin). Old rows kept in `grants_legacy`.
   - collections named 全部商家 are linked as the owner's managed collection.
   - the batch password scope's address books are resolved to the device IDs they hold now, so reconcile cannot widen the batch push (old scope kept in state.json `scope_before_merchants`).
   - reconcile: managed collections get the devices the person can access (alias, platform, merchant tag; other tags kept; exact duplicates removed).
3. Start the sidecar; verify pages, connect, passwordless, push.
4. Rollback: `TS=<ts> bash rollback-merchant.sh` (code + state.json + adoluna.db; `RESTORE_API_DB=1` also restores rustdeskapi.db).
Verified on a copy of prod: password payload, role and access for every existing device/user are identical before and after.
