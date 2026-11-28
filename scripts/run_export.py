#!/usr/bin/env python3
"""Runs the game's clip exporter (tools/cliprender/export_viewer.py, from the encrypted kit) with Mojang's Java assets
fetched from Mojang's own public servers, the way the official launcher gets them:

  piston-meta.mojang.com version manifest -> the latest release's version JSON -> its asset index (written to
  $MINECRAFT_DIR/assets/indexes/) and client jar (sha1 checked, $MINECRAFT_DIR/versions/<v>/<v>.jar, only the font is
  read from it); single sound objects come from resources.download.minecraft.net on first use (sha1 checked).

Nothing fetched here is cached by Actions or leaves the runner unencrypted: it lives in $MINECRAFT_DIR on the runner,
and the finished bundle is age-encrypted before upload. Bedrock textures come from Mojang/bedrock-samples through the
exporter itself (cliprender/assets.py).

  run_export.py <kit dir> <export_viewer.py args...>
Env: MINECRAFT_DIR (required). Output is the exporter's own (the workflow sends it to an encrypted log file).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
OBJECTS = "https://resources.download.minecraft.net"
MOJANG_HOSTS = ("https://piston-meta.mojang.com/", "https://piston-data.mojang.com/", "https://launchermeta.mojang.com/",
                "https://launcher.mojang.com/", OBJECTS + "/")


def get(url: str, sha1: str | None = None, timeout: int = 60) -> bytes:
    if not url.startswith(MOJANG_HOSTS):
        raise SystemExit(f"refusing a non-Mojang URL")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "trove-cam-runner"}), timeout=timeout) as r:  # noqa: S310
        data = r.read()
    if sha1 and hashlib.sha1(data).hexdigest() != sha1:  # noqa: S324 (Mojang publishes sha1)
        raise SystemExit("Mojang download checksum mismatch")
    return data


def prepare(mc: Path) -> None:
    """Asset index + client jar of the latest release into a launcher-style folder."""
    if list((mc / "assets/indexes").glob("*.json")) and list((mc / "versions").glob("*/*.jar")):
        return
    man = json.loads(get(MANIFEST))
    rel = man["latest"]["release"]
    v = next(x for x in man["versions"] if x["id"] == rel)
    ver = json.loads(get(v["url"], v.get("sha1")))
    idx = ver["assetIndex"]
    (mc / "assets/indexes").mkdir(parents=True, exist_ok=True)
    (mc / "assets/indexes" / f"{idx['id']}.json").write_bytes(get(idx["url"], idx.get("sha1")))
    jar = ver["downloads"]["client"]
    (mc / "versions" / rel).mkdir(parents=True, exist_ok=True)
    (mc / "versions" / rel / f"{rel}.jar").write_bytes(get(jar["url"], jar.get("sha1"), timeout=180))
    print(f"mojang assets: release index ready", flush=True)


def lazy_objects(mclocal, mc: Path) -> None:
    """Make cliprender.mclocal.asset_bytes download a missing object (sha1 = its hash) on first use."""
    original = mclocal.asset_bytes

    def asset_bytes(key: str):
        b = original(key)
        if b:
            return b
        o = mclocal.asset_index().get(key)
        if not o:
            return None
        h = o["hash"]
        p = mc / "assets" / "objects" / h[:2] / h
        try:
            data = get(f"{OBJECTS}/{h[:2]}/{h}", h, timeout=30)
        except Exception:  # noqa: BLE001 (one missing sound must not stop the export)
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return data

    mclocal.asset_bytes = asset_bytes


def numpy_json() -> None:
    """export_viewer.py (cliprender v2.7) writes clip.json with json.dumps; on a run that extracts the region from --world-db
    (always the case here: the runner starts with an empty cache) some values are numpy integers and json.dumps stops with
    "Object of type int64 is not JSON serializable" (a cached region loads them as plain ints, so the PC never saw it).
    Until that is fixed in cliprender, numpy scalars / arrays are written as the plain numbers they are."""
    original = json.JSONEncoder.default

    def default(self, o):
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return original(self, o)

    json.JSONEncoder.default = default


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: run_export.py <kit dir> <export_viewer.py args...>")
    numpy_json()
    kit = Path(argv[0]).resolve()
    mc = Path(os.environ["MINECRAFT_DIR"]).resolve()
    prepare(mc)
    tool = kit / "tools" / "cliprender"
    sys.path.insert(0, str(tool))
    from cliprender import mclocal  # noqa: E402 (from the kit)
    lazy_objects(mclocal, mc)
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_viewer", tool / "export_viewer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = ["export_viewer.py", *argv[1:]]
    mod.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
