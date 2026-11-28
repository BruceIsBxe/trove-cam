#!/usr/bin/env python3
"""Renders a #reports clip to an MP4 with the game's own clip renderer (tools/cliprender/render_clip.py, from the
encrypted clip kit) instead of filming it with a camera client, then fits the MP4 under Discord's upload cap.

Mojang's Java assets (sounds, font) come from Mojang's own servers at run time, exactly as for the bundle job
(scripts/run_export.py: prepare / lazy_objects / numpy_json); Bedrock textures come from Mojang/bedrock-samples through
the renderer itself. Nothing fetched here is cached by Actions or leaves the runner unencrypted.

  run_render.py probe                    find a working headless OpenGL context (EGL, any device), print its renderer;
                                         exit 1 when there is none (the workflow then uses Xvfb + GLX)
  run_render.py <kit dir> --clip <clip.json.gz> --skins <dir> --world-db <db> --cache <dir> --out <dir> --tmp <dir>
                [--opts <render.json>] [--max-mb 9.0]
      <out>/clip.mp4  the video (<= max-mb MiB)
      <out>/meta.json {bytes, seconds, width, height, view, render_s, encode_s, passes, gl}  (no names, no ids)

render.json (optional, inside the encrypted clip input, written by the bot):
  {"view": "auto" | "split" | "server" | "player", "viewOf": <player name or null>, "maxMb": 9.0,
   "size": "1280x720", "fps": 30, "msaa": 4, "shadow": 4096, "end": <seconds, tests only>}
  --set key=value (the workflow's CPU settings) wins over render.json.
  auto = SERVER | <viewOf>'s view side by side when the clip has two or more players, else the server view alone.

PRIVACY: the renderer's own output (player names, coordinates) is printed here; the workflow sends this script's output
to the encrypted log only. meta.json carries only sizes and seconds, and is the only thing the workflow prints.
Env: MINECRAFT_DIR (required for a render).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VIEWS = ("auto", "split", "server", "player")
SIZE = re.compile(r"^(\d{3,4})x(\d{3,4})$")
AUDIO_KBPS = 96
MUX_KBPS = 8  # mp4 container overhead, generous for these lengths
MAX_VKBPS = 4000  # a short clip does not need more than this at 720p30
MIN_VKBPS = 300
# CPU (Mesa llvmpipe) budget: cliprender's GPU defaults (30 fps here, MSAA 4, a 4096 shadow map drawn per view per frame)
# took 0.95 s a frame on a GitHub runner = 28 min for a 60 s split clip (selftest 2026-11-28, run timed out).
DEFAULTS = {"fps": 30, "msaa": 4, "shadow": 4096}


def probe() -> int:
    """A standalone GL 3.3 context: EGL with each device index, else nothing (exit 1)."""
    import moderngl
    for idx in (None, 0, 1, 2, 3):
        if idx is not None:
            os.environ["GLCONTEXT_DEVICE_INDEX"] = str(idx)
        try:
            ctx = moderngl.create_standalone_context(require=330, backend="egl")
        except Exception as e:  # noqa: BLE001
            print(f"egl device {idx if idx is not None else 'default'}: {type(e).__name__}: {str(e)[:120]}")
            continue
        print(f"egl device {idx if idx is not None else 'default'}: {ctx.info.get('GL_RENDERER')} / {ctx.info.get('GL_VERSION')}")
        ctx.release()
        gh_env = os.environ.get("GITHUB_ENV")
        if gh_env:
            with open(gh_env, "a", encoding="utf-8") as f:
                f.write("CLIPRENDER_GL_BACKEND=egl\n")
                if idx is not None:
                    f.write(f"GLCONTEXT_DEVICE_INDEX={idx}\n")
        return 0
    return 1


def clean_opts(raw: object, names: list[str]) -> dict:
    """render.json -> the options used, every value checked. Pure."""
    o = raw if isinstance(raw, dict) else {}
    view = o.get("view") if o.get("view") in VIEWS else "auto"
    want = o.get("viewOf")
    view_of = next((n for n in names if isinstance(want, str) and n.lower() == want.strip().lower()), None)
    try:
        max_mb = min(24.0, max(1.0, float(o.get("maxMb", 9.0))))
    except (TypeError, ValueError):
        max_mb = 9.0
    size = o.get("size") if isinstance(o.get("size"), str) and SIZE.match(o.get("size")) else "1280x720"
    w, h = (int(v) for v in size.split("x"))
    if not (320 <= w <= 1920 and 240 <= h <= 1080) or w % 4 or h % 2:
        size = "1280x720"
    fps = o.get("fps") if o.get("fps") in (20, 24, 25, 30, 60) else DEFAULTS["fps"]
    msaa = o.get("msaa") if o.get("msaa") in (0, 1, 2, 4, 8) else DEFAULTS["msaa"]
    shadow = o.get("shadow") if o.get("shadow") in (512, 1024, 2048, 4096) else DEFAULTS["shadow"]
    end = o.get("end") if isinstance(o.get("end"), (int, float)) and 0 < o.get("end") <= 600 else None
    if view == "auto":
        view = "split" if len(names) >= 2 else "server"
    if view == "split" and len(names) < 2:
        view = "server"
    return {"view": view, "viewOf": view_of, "maxMb": max_mb, "size": size, "fps": fps, "msaa": msaa, "shadow": shadow, "end": end}


def parse_sets(items: list[str]) -> dict:
    """--set key=value (numbers as numbers) -> dict; the workflow's choices win over render.json. Pure."""
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[k.strip()] = int(v) if re.fullmatch(r"-?\d+", v.strip()) else float(v) if re.fullmatch(r"-?\d+\.\d*", v.strip()) else v.strip()
        except ValueError:
            continue
    return out


def video_kbps(seconds: float, max_mb: float) -> int:
    """The video bitrate that fits max_mb MiB with the audio and the container. Pure."""
    total = max_mb * 8 * 1048576 / max(1.0, seconds) / 1000
    return int(min(MAX_VKBPS, max(MIN_VKBPS, total - AUDIO_KBPS - MUX_KBPS)))


def scale_for(kbps: int, w: int, h: int, fps: float) -> tuple[int, int]:
    """Smaller frames when the bitrate is too thin for the size (long clips). Pure."""
    bpp = kbps * 1000 / (w * h * fps)
    if bpp >= 0.035:
        return w, h
    f = 0.75 if bpp >= 0.02 else 2 / 3
    return int(w * f) // 4 * 4, int(h * f) // 2 * 2


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration(ff: str, path: Path) -> float:
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise SystemExit("render: cannot read the video's duration")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def encode(ff: str, src: Path, dst: Path, tmp: Path, kbps: int, size: tuple[int, int] | None) -> None:
    """Two-pass x264 at kbps (peaks capped at 1.5x), AAC 96k, faststart."""
    vf = ["-vf", f"scale={size[0]}:{size[1]}:flags=lanczos"] if size else []
    plog = str(tmp / "x264pass")
    common = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *vf, "-c:v", "libx264", "-preset", "medium",
              "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p",
              "-passlogfile", plog]
    subprocess.run([*common, "-pass", "1", "-an", "-f", "null", os.devnull], check=True)
    subprocess.run([*common, "-pass", "2", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-movflags", "+faststart", str(dst)], check=True)


def fit(ff: str, src: Path, dst: Path, tmp: Path, max_mb: float, w: int, h: int, fps: float) -> dict:
    """Encode src into dst under max_mb MiB: the computed bitrate, then 85 % of it up to two more times."""
    secs = duration(ff, src)
    limit = int(max_mb * 1048576)
    kbps = video_kbps(secs, max_mb)
    passes = 0
    for _ in range(3):
        size = scale_for(kbps, w, h, fps)
        encode(ff, src, dst, tmp, kbps, None if size == (w, h) else size)
        passes += 1
        if dst.stat().st_size <= limit:
            return {"seconds": round(secs, 2), "width": size[0], "height": size[1], "kbps": kbps, "passes": passes}
        print(f"encode: {dst.stat().st_size} bytes > {limit} at {kbps}k, again at 85 %", flush=True)
        kbps = max(150, int(kbps * 0.85))
    raise SystemExit(f"render: the video stays over {max_mb} MiB")


def clip_names(clip: Path) -> list[str]:
    doc = json.loads(gzip.decompress(clip.read_bytes()).decode("utf-8"))
    return [str(a.get("name", "")) for a in doc.get("actors", []) if isinstance(a, dict) and a.get("name")]


def render(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("kit")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--skins", required=True)
    ap.add_argument("--world-db", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tmp", required=True)
    ap.add_argument("--opts", default=None)
    ap.add_argument("--max-mb", type=float, default=None)
    ap.add_argument("--set", action="append", default=[], help="key=value over render.json (fps, msaa, shadow, size, end, view)")
    a = ap.parse_args(argv)
    import run_export  # the same Mojang asset path as the bundle job
    run_export.numpy_json()
    kit = Path(a.kit).resolve()
    out, tmp = Path(a.out), Path(a.tmp)
    out.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    names = clip_names(Path(a.clip))
    raw = {}
    if a.opts and Path(a.opts).is_file():
        try:
            raw = json.loads(Path(a.opts).read_text(encoding="utf-8"))
        except ValueError:
            raw = {}
    o = clean_opts({**(raw if isinstance(raw, dict) else {}), **parse_sets(a.set)}, names)
    if a.max_mb:
        o["maxMb"] = min(24.0, max(1.0, a.max_mb))
    print(f"render: {len(names)} players, view {o['view']}{' of ' + o['viewOf'] if o['viewOf'] else ''}, {o['size']} @ {o['fps']}, "
          f"msaa {o['msaa']}, shadow {o['shadow']}{', end ' + str(o['end']) + ' s' if o['end'] else ''}", flush=True)
    mc = Path(os.environ["MINECRAFT_DIR"]).resolve()
    run_export.prepare(mc)
    tool = kit / "tools" / "cliprender"
    if not (tool / "render_clip.py").is_file():
        raise SystemExit("render: the kit has no tools/cliprender/render_clip.py (rebuild the clip kit)")
    sys.path.insert(0, str(tool))
    from cliprender import mclocal  # noqa: E402 (from the kit)
    run_export.lazy_objects(mclocal, mc)
    import importlib.util
    spec = importlib.util.spec_from_file_location("render_clip", tool / "render_clip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from cliprender import renderer  # noqa: E402 (from the kit): render_clip.py passes no shadow size, so set the default
    d = list(renderer.Renderer.__init__.__defaults__ or ())
    if len(d) == 2:  # (samples, shadow_size)
        renderer.Renderer.__init__.__defaults__ = (d[0], o["shadow"])
    raw_mp4 = tmp / "render.mp4"
    args = ["--clip", a.clip, "--skins", a.skins, "--world-db", a.world_db, "--cache", a.cache, "--out", str(raw_mp4),
            "--fps", str(o["fps"]), "--size", o["size"], "--msaa", str(o["msaa"]), "--crf", "16", "--preset", "veryfast",
            "--tmp", str(tmp / "cliprender")]
    if o["view"] == "split":
        args.append("--split")
    elif o["view"] == "player":
        args += ["--view", "player"]
    if o["viewOf"] and o["view"] in ("split", "player"):
        args += ["--view-of", o["viewOf"]]
    if o["end"]:
        args += ["--end", str(o["end"])]
    sys.argv = ["render_clip.py", *args]
    t0 = time.time()
    mod.main()
    render_s = time.time() - t0
    if not raw_mp4.is_file() or raw_mp4.stat().st_size < 1024:
        raise SystemExit("render: no video came out")
    ff = ffmpeg_exe()
    w, h = (int(v) for v in o["size"].split("x"))
    t1 = time.time()
    enc = fit(ff, raw_mp4, out / "clip.mp4", tmp, o["maxMb"], w, h, o["fps"])
    encode_s = time.time() - t1
    meta = {"bytes": (out / "clip.mp4").stat().st_size, **enc, "view": o["view"], "fps": o["fps"], "msaa": o["msaa"], "shadow": o["shadow"],
            "render_s": round(render_s, 1), "encode_s": round(encode_s, 1), "gl": gl_name()}
    (out / "meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    print(f"render: done {json.dumps(meta)}", flush=True)
    return 0


def gl_name() -> str:
    try:
        import moderngl
        b = os.environ.get("CLIPRENDER_GL_BACKEND")
        ctx = moderngl.create_standalone_context(require=330, **({"backend": b} if b else {}))
        name = str(ctx.info.get("GL_RENDERER", "?"))[:60]
        ctx.release()
        return name
    except Exception:  # noqa: BLE001
        return "?"


def main(argv: list[str]) -> int:
    if argv[:1] == ["probe"]:
        return probe()
    if not argv:
        raise SystemExit(__doc__)
    return render(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
