#!/bin/bash
# Watchdog for adoluna-pwpush (run every minute by adoluna-pwpush-health.timer).
# Probes /_adoluna/health; after 2 consecutive failures restarts the sidecar and logs why.
U="http://${PW_LISTEN:-10.20.26.62}:${PW_PORT:-21114}/_adoluna/health"; F=/run/adoluna-pwpush-health.fail
systemctl is-active -q adoluna-pwpush || exit 0   # stopped on purpose (deploy/rollback): leave it
if /usr/bin/python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(sys.argv[1], timeout=10).status==200 else 1)" "$U" 2>/dev/null; then
  rm -f "$F"; exit 0; fi
n=$(( $(cat "$F" 2>/dev/null || echo 0) + 1 )); echo $n > "$F"
P=$(systemctl show -p MainPID --value adoluna-pwpush)
echo "health check failed ($n) fds=$(ls /proc/$P/fd 2>/dev/null | wc -l) closewait=$(ss -tan state close-wait 2>/dev/null | tail -n +2 | wc -l)"
if [ "$n" -ge 2 ]; then echo "restarting adoluna-pwpush"; rm -f "$F"; systemctl restart adoluna-pwpush; fi
