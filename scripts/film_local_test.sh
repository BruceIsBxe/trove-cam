#!/usr/bin/env bash
# Test harness for scripts/film.sh: start the throwaway local PocketMine (CamTest scene) and film it with the
# production script, so the join detection, trimming and encryption are exercised without the real server.
set -uo pipefail
PM_DIR=${PM_DIR:?PM_DIR}
OUT=${OUT:?OUT}
mkdir -p "$OUT"
( cd "$PM_DIR" && export CAMTEST_SPEED=1 CAMTEST_CAMERA=CamTest && exec ./bin/php7/bin/php PocketMine-MP.phar --no-wizard --disable-ansi ) > "$OUT/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT
for i in $(seq 1 120); do grep -q "Done (" "$OUT/server.log" && break; sleep 1; done
grep -q "Done (" "$OUT/server.log" || { echo "local server did not start"; exit 1; }
echo "local server ready"
SERVER_ADDR=127.0.0.1:19132 CAM_NAME=CamTest CAM_SECONDS=${RECORD_SECONDS:-20} bash "$(dirname "$0")/film.sh"
