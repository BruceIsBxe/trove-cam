#!/usr/bin/env bash
# Production camera run: Cinnabar under Xvfb joins the real server as a one-time camera name, the server plays the
# clip's replay to that viewer only (3 s pre-roll after the client spawns, then the clip), and ffmpeg films it.
#
# PRIVACY: this repository is public. Console output (= the public Actions log) carries stage names and numbers
# only; the address, camera name, clip id and every log stay in $OUT, which leaves the runner only age-encrypted.
#
# Env: SERVER_ADDR, CAM_NAME, CAM_SECONDS (clip length), CINNABAR (AppRun), OUT,
#      WIDTH/HEIGHT (854x480), CAPTURE_FPS (30), CRF (28), JOIN_TIMEOUT (480), READY_TIMEOUT (240),
#      PRE_ROLL (3, must match the server), TAIL (6 = seconds kept after the clip).
set -uo pipefail

ADDR=${SERVER_ADDR:?SERVER_ADDR}
CAM=${CAM_NAME:?CAM_NAME}
SECS=${CAM_SECONDS:?CAM_SECONDS}
CINNABAR=${CINNABAR:?CINNABAR}
OUT=${OUT:?OUT}
W=${WIDTH:-854}
H=${HEIGHT:-480}
CAPTURE_FPS=${CAPTURE_FPS:-30}
CRF=${CRF:-28}
JOIN_TIMEOUT=${JOIN_TIMEOUT:-480}
READY_TIMEOUT=${READY_TIMEOUT:-240}
PRE_ROLL=${PRE_ROLL:-3}
TAIL=${TAIL:-6}
DISP=:99
CLIENT_LOG="$HOME/.local/share/cinnabar/logs/client.log"

mkdir -p "$OUT/shots"
exec 3>&1 >>"$OUT/run.log" 2>&1
T0=$(date +%s)
log() { echo "[$(date -u +%H:%M:%S.%N | cut -c1-12)] [+$(( $(date +%s) - T0 ))s] $*" >> "$OUT/timeline.log"; }
say() { log "$*"; echo "[+$(( $(date +%s) - T0 ))s] $*" >&3; }   # stage names and numbers only
mark() { echo "$1=$(( $(date +%s) - T0 ))" >> "$OUT/marks.env"; }
shot() { ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "${W}x${H}" -i "$DISP" -frames:v 1 "$OUT/shots/$1.png" || true; }
inlog() { grep -aq "$1" "$CLIENT_LOG" 2>/dev/null; }
PIDS=()
cleanup() {
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  pkill -f bedrock-core 2>/dev/null || true
  pulseaudio --kill 2>/dev/null || true
  sleep 1
  for p in "${PIDS[@]}"; do kill -9 "$p" 2>/dev/null || true; done
}
trap cleanup EXIT
fail() {
  local why=$1 disc
  # the client's own disconnect line (e.g. the server's kick reason), if any, goes into the encrypted bundle only
  disc=$(grep -aiE "disconnect|kicked" "$CLIENT_LOG" 2>/dev/null | tail -1 | sed -E 's/^[0-9TZ:.-]+ +[A-Z]+ +//' | cut -c1-160)
  say "FAILED: $why"; shot fail
  { echo "$why"; [ -n "$disc" ] && echo "client: $disc"; } > "$OUT/failure.txt"
  exit 1
}

say "runner: $(nproc) vCPU, ${W}x${H}, clip ${SECS}s"
rm -f "$CLIENT_LOG"

# HUD hidden from the first frame (Cinnabar persists F1 as hide_hud); keep any other saved settings
mkdir -p "$HOME/.config/cinnabar"
python3 - "$HOME/.config/cinnabar/settings.json" <<'PY'
import json, os, sys
p = sys.argv[1]
try:
    s = json.load(open(p))
except Exception:
    s = {"schema": 2}
s.setdefault("values", {})["hide_hud"] = 1
json.dump(s, open(p, "w"), indent=2)
PY

Xvfb "$DISP" -screen 0 "${W}x${H}x24" -nolisten tcp -noreset > "$OUT/xvfb.log" 2>&1 &
PIDS+=("$!"); export DISPLAY=$DISP
for i in $(seq 1 30); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
pidstat -u -h -l 2 > "$OUT/pidstat.log" 2>&1 & PIDS+=("$!")

# Sound: the runner has no sound card. A PulseAudio null sink is the client's output (ALSA default -> pulse);
# ffmpeg records its monitor together with the screen. Without pulseaudio (or AUDIO=0) the clip is silent.
AUDIO=${AUDIO:-1}
if [ "$AUDIO" = 1 ] && ! command -v pulseaudio >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends     pulseaudio pulseaudio-utils libasound2-plugins > "$OUT/apt-audio.log" 2>&1 || true
fi
if [ "$AUDIO" = 1 ] && command -v pulseaudio >/dev/null 2>&1; then
  printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > "$HOME/.asoundrc"
  pulseaudio -n --daemonize=yes --exit-idle-time=-1 --log-target=file:"$OUT/pulse.log" \
    --load="module-null-sink sink_name=cam rate=48000 channels=2" --load="module-native-protocol-unix" \
    >/dev/null 2>&1 || true
  for i in $(seq 1 20); do pactl info >/dev/null 2>&1 && break; sleep 0.25; done
  if pactl info >/dev/null 2>&1; then pactl set-default-sink cam >/dev/null 2>&1 || true; else AUDIO=0; fi
else
  AUDIO=0
fi
say "audio: $AUDIO"

export CINNABAR_ACCEPT_MOJANG_EULA=1
export RUST_LOG=${RUST_LOG:-info,wgpu_core=warn,wgpu_hal=warn,naga=warn}
export WGPU_BACKEND=vulkan
ICD=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json 2>/dev/null | head -1)
[ -n "$ICD" ] && export VK_ICD_FILENAMES=$ICD
say "launching Cinnabar"
"$CINNABAR" --address "$ADDR" --display-name "$CAM" --gui-scale 2 > "$OUT/cinnabar.log" 2>&1 &
CIN_PID=$!; PIDS+=("$CIN_PID")
mark launched

# 1. logged in (StartGame received; server resource packs come before this)
while ! inlog "received StartGame"; do
  kill -0 "$CIN_PID" 2>/dev/null || fail "client exited before joining"
  [ $(( $(date +%s) - T0 )) -lt "$JOIN_TIMEOUT" ] || fail "join timeout"
  sleep 0.5
done
mark start_game; say "logged in"

WIN=$(xdotool search --onlyvisible --pid "$CIN_PID" 2>/dev/null | head -1)
[ -z "$WIN" ] && WIN=$(xdotool search --onlyvisible --name . 2>/dev/null | head -1)
if [ -n "$WIN" ]; then
  xdotool windowmove "$WIN" 0 0 windowsize "$WIN" "$W" "$H" 2>/dev/null || true
  xdotool windowfocus "$WIN" 2>/dev/null || true
fi

# 2. record from now (the client finishes its shader warmup, then spawns; the server starts the pre-roll then)
REC0=$(date +%s.%N)
AIN=(); AENC=()
if [ "$AUDIO" = 1 ]; then AIN=(-thread_queue_size 1024 -f pulse -i cam.monitor); AENC=(-c:a pcm_s16le); fi
ffmpeg -loglevel warning -y -thread_queue_size 512 -f x11grab -draw_mouse 0 -framerate "$CAPTURE_FPS" -video_size "${W}x${H}" -i "$DISP" \
  "${AIN[@]}" -c:v libx264 -preset ultrafast -crf 18 -pix_fmt yuv420p "${AENC[@]}" "$OUT/raw.mkv" > "$OUT/ffmpeg-record.log" 2>&1 &
FF_PID=$!; PIDS+=("$FF_PID")
while ! inlog "pipeline warmup finished"; do
  kill -0 "$CIN_PID" 2>/dev/null || fail "client exited while loading"
  [ $(( $(date +%s) - T0 )) -lt $((JOIN_TIMEOUT + READY_TIMEOUT)) ] || fail "ready timeout"
  sleep 0.25
done
READY=$(date +%s.%N)
mark ready; say "client ready, filming"

# 3. pre-roll + clip + tail, then stop
END=$(python3 -c "import sys; print(float(sys.argv[1]) + $PRE_ROLL + $SECS + $TAIL)" "$READY")
while python3 -c "import sys,time; sys.exit(0 if time.time() < float(sys.argv[1]) else 1)" "$END"; do
  kill -0 "$CIN_PID" 2>/dev/null || { log "client exited during filming"; break; }
  sleep 0.5
done
kill -INT "$FF_PID" 2>/dev/null; wait "$FF_PID" 2>/dev/null
mark recorded; say "recording stopped"
kill "$CIN_PID" 2>/dev/null || true

# 4. cut to [ready + 0.3 s, end] (drops the last loading-screen frames; the server pre-roll starts ~0.7 s before ready) and encode
START=$(python3 -c "import sys; print(max(0.0, float(sys.argv[1]) - float(sys.argv[2]) + 0.3))" "$READY" "$REC0")
LEN=$(python3 -c "print($PRE_ROLL + $SECS + $TAIL - 0.3)")
ffmpeg -loglevel warning -y -ss "$START" -i "$OUT/raw.mkv" -t "$LEN" -r 30 -c:v libx264 -preset medium -crf "$CRF" \
  -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart "$OUT/clip.mp4" || fail "encode failed"
rm -f "$OUT/raw.mkv"
mark encoded
[ -s "$OUT/clip.mp4" ] || fail "empty clip"
cp -r "$HOME/.local/share/cinnabar/logs" "$OUT/cinnabar-logs" 2>/dev/null || true
python3 - "$OUT" <<'PY' >&3
import json, os, sys
out = sys.argv[1]
marks = dict(l.strip().split("=", 1) for l in open(os.path.join(out, "marks.env")))
print(json.dumps({"marks_s": {k: int(v) for k, v in marks.items()},
                  "clip_bytes": os.path.getsize(os.path.join(out, "clip.mp4"))}))
PY
say "done"
