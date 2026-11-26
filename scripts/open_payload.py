#!/usr/bin/env python3
"""Decrypt the dispatch payload (base64 of an age ciphertext to .github/cam-runner-recipient.txt) on the runner.

Env: PAYLOAD (the workflow input), CAM_RUNNER_AGE_IDENTITY (repository secret), GITHUB_ENV.
Masks every value first (::add-mask::), validates it, then exports CAM_* variables to later steps.
Usage: open_payload.py film|ping
Prints nothing but "payload ok" / a short failure reason.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile

KIND = sys.argv[1] if len(sys.argv) > 1 else "film"


def die(msg: str) -> None:
    print(f"payload rejected: {msg}")
    sys.exit(1)


def main() -> None:
    identity = os.environ.get("CAM_RUNNER_AGE_IDENTITY", "").strip()
    if not identity:
        die("CAM_RUNNER_AGE_IDENTITY secret is not set")
    try:
        blob = base64.b64decode(os.environ.get("PAYLOAD", "").strip(), validate=True)
    except Exception:
        die("payload is not base64")
    if len(blob) > 8192:
        die("payload too large")
    with tempfile.TemporaryDirectory() as tmp:
        idf = os.path.join(tmp, "id")
        fd = os.open(idf, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(identity + "\n")
        r = subprocess.run(["age", "-d", "-i", idf], input=blob, capture_output=True)
    if r.returncode != 0:
        die("cannot decrypt (wrong recipient or corrupted)")
    try:
        data = json.loads(r.stdout.decode("utf-8"))
    except Exception:
        die("not JSON")
    if not isinstance(data, dict) or data.get("v") != 1:
        die("unknown payload version")

    out = {}
    if KIND == "film":
        cam, clip = str(data.get("cam", "")), str(data.get("clip", ""))
        view = str(data.get("view", "pov"))
        for value in (cam, clip):
            if value:
                print(f"::add-mask::{value}")
        if not re.fullmatch(r"cam[a-z0-9]{10}", cam):
            die("bad cam name")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", clip):
            die("bad clip id")
        if view not in ("pov", "chase"):
            die("bad view")
        try:
            seconds = int(data.get("seconds", 30))
        except Exception:
            die("bad seconds")
        if not 1 <= seconds <= 60:
            die("seconds out of range")
        out = {"CAM_NAME": cam, "CAM_CLIP": clip, "CAM_VIEW": view, "CAM_SECONDS": str(seconds)}
    elif KIND == "ping":
        addr = str(data.get("addr", ""))
        if addr:
            print(f"::add-mask::{addr}")
            print(f"::add-mask::{addr.rpartition(':')[0]}")
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}:[0-9]{1,5}", addr):
            die("bad address")
        out = {"PING_ADDR": addr}
    else:
        die("unknown kind")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
        for k, v in out.items():
            f.write(f"{k}={v}\n")
    print("payload ok")


if __name__ == "__main__":
    main()
