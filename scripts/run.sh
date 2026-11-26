#!/usr/bin/env bash
# Camera run inside a GitHub Actions ubuntu runner:
#   [local]  throwaway PocketMine-MP (NG) on 127.0.0.1 with the CamTest scene + optional bot
#   [remote] a server given by the SERVER_ADDR secret (phase 2; NOT exercised yet)
#   -> Cinnabar under Xvfb joins as CAMERA_NAME -> ffmpeg records the virtual display
#   -> out/clip.mp4 + logs + stats (out/summary.json). The workflow encrypts out/ with age before upload.
#
# PRIVACY: this repository is meant to be public. Console output (= the public Actions log) carries only stage
# names and numbers. Everything detailed (server log, client log, screenshots, names, addresses) goes to files in
# $OUT, which leave the runner only age-encrypted.
#
# Inputs (env): TARGET (local|remote), SERVER_ADDR (remote), CAMERA_NAME, RECORD_SECONDS, WIDTH, HEIGHT,
#               BACKEND (vulkan|gl), SETUP_TIMEOUT, CAPTURE_FPS, CRF, SPEED, SHOW_DEBUG, PM_DIR, CINNABAR, OUT.
set -uo pipefail

TARGET=${TARGET:-local}
CAM=${CAMERA_NAME:-}
[ -z "$CAM" ] && CAM=CamTest
SECS=${RECORD_SECONDS:-20}
W=${WIDTH:-854}
H=${HEIGHT:-480}
BACKEND=${BACKEND:-vulkan}
SETUP_TIMEOUT=${SETUP_TIMEOUT:-1500}
CAPTURE_FPS=${CAPTURE_FPS:-30}
CRF=${CRF:-28}
SPEED=${SPEED:-1}          # scene speed on the local server; <1 = slow motion, sped back up in the video
SHOW_DEBUG=${SHOW_DEBUG:-}
[ -z "$SHOW_DEBUG" ] && { [ "$TARGET" = local ] && SHOW_DEBUG=1 || SHOW_DEBUG=0; }
CINNABAR=${CINNABAR:?CINNABAR}
OUT=${OUT:-$PWD/out}
REPO=${GITHUB_WORKSPACE:-$PWD}
DISP=:99
CLIENT_LOG="$HOME/.local/share/cinnabar/logs/client.log"

mkdir -p "$OUT/shots" "$OUT/frames"
# fd 3 = the public console; everything else goes to run.log (encrypted later)
exec 3>&1 >>"$OUT/run.log" 2>&1
T0=$(date +%s)
log() { echo "[$(date -u +%H:%M:%S)] [+$(( $(date +%s) - T0 ))s] $*" >> "$OUT/timeline.log"; }
say() { log "$*"; echo "[+$(( $(date +%s) - T0 ))s] $*" >&3; }   # only stage names and numbers, never names/addresses
mark() { echo "$1=$(( $(date +%s) - T0 ))" >> "$OUT/marks.env"; }
shot() { ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "${W}x${H}" -i "$DISP" -frames:v 1 "$OUT/shots/$1.png" || true; }
lastframe() { grep -aoE "CINNABAR_SLOW_FRAME frame=[0-9]+" "$CLIENT_LOG" 2>/dev/null | tail -1 | grep -oE "[0-9]+$" || echo 0; }
fail() { say "FAILED: $1 (details only in the encrypted logs)"; shot fail; exit 1; }
REC=$(python3 -c "import math,sys; print(math.ceil(float(sys.argv[1])/float(sys.argv[2])))" "$SECS" "$SPEED")
PIDS=()
cleanup() {
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  pkill -f bedrock-core 2>/dev/null || true
  sleep 1
  for p in "${PIDS[@]}"; do kill -9 "$p" 2>/dev/null || true; done
}
trap cleanup EXIT

nproc > "$OUT/nproc.txt"; lscpu > "$OUT/lscpu.txt" 2>&1; free -m > "$OUT/free.txt"
say "runner: $(nproc) vCPU, $(free -g | awk '/Mem:/{print $2}') GB, target=$TARGET ${W}x${H} speed=$SPEED"

# ---- 1. server -------------------------------------------------------------------------------
if [ "$TARGET" = local ]; then
  PM_DIR=${PM_DIR:?PM_DIR}
  ADDR=127.0.0.1:19132
  ( cd "$PM_DIR" && export CAMTEST_SPEED="$SPEED" CAMTEST_CAMERA="$CAM" && exec ./bin/php7/bin/php PocketMine-MP.phar --no-wizard --disable-ansi ) > "$OUT/server.log" 2>&1 &
  SERVER_PID=$!; PIDS+=("$SERVER_PID")
  for i in $(seq 1 180); do
    grep -q "Done (" "$OUT/server.log" && break
    kill -0 "$SERVER_PID" 2>/dev/null || fail "local server died"
    sleep 1
  done
  grep -q "Done (" "$OUT/server.log" || fail "local server did not start"
  mark server_ready; say "local server ready"
  grep -q "CAMTEST enabled" "$OUT/server.log" || say "WARNING scene plugin did not load"
else
  ADDR=${SERVER_ADDR:-}
  [ -n "$ADDR" ] || fail "TARGET=remote needs the SERVER_ADDR secret"
  say "remote target (address from secret)"
fi

# ---- 2. display + monitors -------------------------------------------------------------------
Xvfb "$DISP" -screen 0 "${W}x${H}x24" -nolisten tcp -noreset > "$OUT/xvfb.log" 2>&1 &
PIDS+=("$!"); export DISPLAY=$DISP
for i in $(seq 1 30); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
vulkaninfo --summary > "$OUT/vulkaninfo.txt" 2>&1 || true
glxinfo -B > "$OUT/glxinfo.txt" 2>&1 || true
vmstat -n -t 2 > "$OUT/vmstat.log" 2>&1 & PIDS+=("$!")
pidstat -u -h -l 2 > "$OUT/pidstat.log" 2>&1 & PIDS+=("$!")

# ---- 3. Cinnabar -----------------------------------------------------------------------------
export CINNABAR_ACCEPT_MOJANG_EULA=1
export RUST_LOG=${RUST_LOG:-info,wgpu_core=warn,wgpu_hal=warn,naga=warn}
export RUST_BACKTRACE=1
export WGPU_BACKEND=$BACKEND
if [ "$BACKEND" = vulkan ]; then
  ICD=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json 2>/dev/null | head -1)
  [ -n "$ICD" ] && export VK_ICD_FILENAMES=$ICD
fi
say "launching Cinnabar ($BACKEND)"
"$CINNABAR" --address "$ADDR" --display-name "$CAM" --gui-scale 2 > "$OUT/cinnabar.log" 2>&1 &
CIN_PID=$!; PIDS+=("$CIN_PID")
mark cinnabar_launched

# wait until the camera is in the world (first run also prepares Mojang's assets)
joined=0; n=0
while [ $(( $(date +%s) - T0 )) -lt "$SETUP_TIMEOUT" ]; do
  if [ "$TARGET" = local ]; then
    grep -q "CAMTEST ready" "$OUT/server.log" && { joined=1; break; }
  else
    grep -aq "pipeline warmup finished" "$CLIENT_LOG" 2>/dev/null && grep -aq "received StartGame" "$CLIENT_LOG" 2>/dev/null && { joined=1; break; }
  fi
  kill -0 "$CIN_PID" 2>/dev/null || { wait "$CIN_PID"; log "Cinnabar exited early (code $?)"; break; }
  n=$((n + 1))
  if [ $((n % 15)) -eq 0 ]; then shot "wait-$(printf %04d $(( $(date +%s) - T0 )))"; say "waiting for the camera to join"; fi
  sleep 2
done
xwininfo -root -tree > "$OUT/xwininfo.txt" 2>&1 || true
[ "$joined" = 1 ] || fail "camera did not join"
mark camera_joined; say "camera joined"

# ---- 4. second player (local scene only) -----------------------------------------------------
if [ "$TARGET" = local ] && [ -d "$REPO/bot/node_modules" ]; then
  ( cd "$REPO/bot" && exec node sparring.mjs --host 127.0.0.1 --port 19132 --name Sparring --seconds $((REC + 40)) ) > "$OUT/bot.log" 2>&1 &
  PIDS+=("$!")
fi

# ---- 5. window + record ----------------------------------------------------------------------
WIN=$(xdotool search --onlyvisible --pid "$CIN_PID" 2>/dev/null | head -1)
[ -z "$WIN" ] && WIN=$(xdotool search --onlyvisible --name . 2>/dev/null | head -1)
log "window id: ${WIN:-none} $(xdotool getwindowgeometry "$WIN" 2>/dev/null | tr '\n' ' ')"
if [ -n "$WIN" ]; then
  xdotool windowmove "$WIN" 0 0 windowsize "$WIN" "$W" "$H" 2>/dev/null || true
  xdotool windowfocus "$WIN" 2>/dev/null || true
fi
sleep 10   # chunks, skins, the bot's arrival
F_PRE=$(lastframe); sleep 5; F_PRE2=$(lastframe); echo "idle_frames_5s=$((F_PRE2 - F_PRE))" >> "$OUT/marks.env"
shot "before-record"
if [ "$SHOW_DEBUG" = 1 ]; then xdotool key F3 2>/dev/null || true; else xdotool key F1 2>/dev/null || true; fi
sleep 1
mark record_start
F0=$(lastframe); RT0=$(date +%s.%N)
say "recording ${REC}s of real time (${SECS}s clip) at ${CAPTURE_FPS} fps capture"
ffmpeg -loglevel warning -y -f x11grab -draw_mouse 0 -framerate "$CAPTURE_FPS" -video_size "${W}x${H}" -i "$DISP" \
  -t "$REC" -c:v libx264 -preset ultrafast -crf 16 -pix_fmt yuv420p "$OUT/raw.mp4" > "$OUT/ffmpeg-record.log" 2>&1 &
FF_PID=$!
if [ "$SHOW_DEBUG" = 1 ]; then
  sleep $((REC / 2))
  xdotool key F3 2>/dev/null || true   # overlay off
  xdotool key F1 2>/dev/null || true   # HUD off: the clean "replay camera" look
fi
wait "$FF_PID"; rc=$?
F1=$(lastframe); RT1=$(date +%s.%N)
python3 -c "import sys; f0,f1,t0,t1=map(float,sys.argv[1:]); print('client_frames=%d' % (f1-f0)); print('client_fps_x100=%d' % round(100*(f1-f0)/(t1-t0)))" "$F0" "$F1" "$RT0" "$RT1" >> "$OUT/marks.env"
mark record_end; say "recording done (ffmpeg $rc)"
shot "after-record"
alive=0; kill -0 "$CIN_PID" 2>/dev/null && alive=1

# ---- 6. encode + measure ---------------------------------------------------------------------
VF="null"; [ "$SPEED" != 1 ] && VF="setpts=${SPEED}*PTS"
ffmpeg -loglevel warning -y -i "$OUT/raw.mp4" -vf "$VF" -r 30 -c:v libx264 -preset slow -crf "$CRF" -pix_fmt yuv420p -movflags +faststart "$OUT/clip.mp4"
mark encoded
ffmpeg -loglevel error -y -i "$OUT/clip.mp4" -vf "fps=1/4" "$OUT/frames/frame-%02d.png" || true
# distinct frames: what the client actually presented (capture is capped at CAPTURE_FPS)
UNIQUE=$(ffmpeg -i "$OUT/raw.mp4" -vf "mpdecimate=hi=64:lo=64:frac=0,showinfo" -an -f null - 2>&1 | grep -c "Parsed_showinfo.*n:" || true)
TOTAL=$(ffmpeg -i "$OUT/raw.mp4" -vf showinfo -an -f null - 2>&1 | grep -c "Parsed_showinfo.*n:" || true)
CLIP_UNIQUE=$(ffmpeg -i "$OUT/clip.mp4" -vf "mpdecimate=hi=64*12:lo=64*5:frac=0.33,showinfo" -an -f null - 2>&1 | grep -c "Parsed_showinfo.*n:" || true)
rm -f "$OUT/raw.mp4"
{ du -sh "$HOME"/.cache/* "$HOME"/.local/share/cinnabar/* "$HOME"/.config/cinnabar 2>/dev/null; } > "$OUT/dirs.txt"
cp -r "$HOME/.config/cinnabar" "$OUT/cinnabar-config" 2>/dev/null || true
cp -r "$HOME/.local/share/cinnabar/logs" "$OUT/cinnabar-logs" 2>/dev/null || true

python3 - "$OUT" "$SECS" "$UNIQUE" "$TOTAL" "$alive" "$BACKEND" "$CAPTURE_FPS" "$SPEED" "$REC" "$CLIP_UNIQUE" "$W" "$H" <<'PY'
import json, os, sys
out, secs, unique, total, alive, backend, cap, speed, rec, clip_unique, w, h = sys.argv[1:]
speed = float(speed); rec = float(rec); secs = float(secs)
unique = int(unique or 0); total = int(total or 0); clip_unique = int(clip_unique or 0)
marks = {}
for line in open(os.path.join(out, "marks.env")):
    k, v = line.strip().split("=", 1); marks[k] = int(v)
cpu = {}
try:
    for line in open(os.path.join(out, "pidstat.log")):
        parts = line.split()
        if len(parts) < 10 or parts[0].startswith("#") or not parts[2].isdigit():
            continue
        try:
            pct = float(parts[7])   # Time UID PID %usr %system %guest %wait %CPU CPU Command...
        except ValueError:
            continue
        cmd = " ".join(parts[9:])
        name = ("cinnabar" if "cinnabar" in cmd.lower() and "bedrock-core" not in cmd else
                "bedrock-core" if "bedrock-core" in cmd else
                "php(server)" if "PocketMine" in cmd or "/php" in cmd else
                "ffmpeg" if cmd.startswith("ffmpeg") or "/ffmpeg" in cmd else
                "Xvfb" if "Xvfb" in cmd else
                "node(bot)" if "sparring" in cmd else None)
        if name:
            cpu.setdefault(name, []).append(pct)
except FileNotFoundError:
    pass
summary = {   # numbers only: printed to the public log
    "resolution": f"{w}x{h}",
    "backend": backend,
    "clip_seconds": secs,
    "scene_speed": speed,
    "real_seconds_recorded": rec,
    "capture_fps": int(cap),
    "client_fps_real_time": marks.get("client_fps_x100", 0) / 100,
    "client_idle_fps_before_record": marks.get("idle_frames_5s", 0) / 5,
    "distinct_fps_capture": round(unique / rec, 2) if rec else None,
    "visible_fps_in_clip": round(clip_unique / secs, 1) if secs else None,
    "cinnabar_alive_after_record": alive == "1",
    "marks_seconds_from_script_start": marks,
    "clip_bytes": os.path.getsize(os.path.join(out, "clip.mp4")) if os.path.exists(os.path.join(out, "clip.mp4")) else None,
    "cpu_pct_of_one_core_avg": {k: round(sum(v) / len(v), 1) for k, v in cpu.items()},
}
json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=2)
PY
cat "$OUT/summary.json" >&3
exit 0
