#!/usr/bin/env python3
"""Runner side of the Da Club bot's map / bundle / render jobs (map.yml, bundle.yml, render.yml).

PRIVACY: this repository is public, so is the Actions log. This script prints stage words only. The dispatch payload
(age, to .github/cam-runner-recipient.txt) carries the bot's address and a one-time token; both are masked at once and
kept in a 0600 file under $RUNNER_TEMP, never in the log or in $GITHUB_ENV.

  runner_job.py open <map|bundle|render>  decrypt + check the payload, write $RUNNER_TEMP/job.json, export KIT_SHA
  runner_job.py fetch <name> <dest> [--cache <file>]
                                          GET <base>/runner/v1/input/<name> (Authorization: Bearer <token>), the body is age
                                          to this runner; decrypt, check its sha256 against the payload, write <dest>.
                                          --cache: use that ciphertext file when it is there (kit cache), else save the
                                          downloaded ciphertext there (it stays encrypted to this runner).
Env: PAYLOAD, CAM_RUNNER_AGE_IDENTITY (repository secret), GITHUB_ENV, RUNNER_TEMP.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

INPUT_NAME = re.compile(r"[a-z]{2,12}")
MAX_INPUT = {"kit": 64 << 20, "world": 96 << 20, "clip": 32 << 20}
KINDS = {"map": {"kit", "world"}, "bundle": {"kit", "world", "clip"}, "render": {"kit", "world", "clip"}}


def die(msg: str) -> None:
    print(f"job: {msg}")
    sys.exit(1)


def job_file() -> Path:
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "trove-job.json"


def age_decrypt(blob: bytes) -> bytes:
    identity = os.environ.get("CAM_RUNNER_AGE_IDENTITY", "").strip()
    if not identity:
        die("CAM_RUNNER_AGE_IDENTITY secret is not set")
    with tempfile.TemporaryDirectory() as tmp:
        idf = os.path.join(tmp, "id")
        fd = os.open(idf, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(identity + "\n")
        r = subprocess.run(["age", "-d", "-i", idf], input=blob, capture_output=True)
    if r.returncode != 0:
        die("cannot decrypt (wrong recipient or corrupted)")
    return r.stdout


def check_payload(data: object, kind: str) -> dict:
    """Validates a decrypted payload. -> the job dict. Pure (raises ValueError)."""
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("unknown payload version")
    if data.get("kind") != kind:
        raise ValueError("payload is for another workflow")
    base = str(data.get("base", ""))
    if not re.fullmatch(r"https://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?", base):
        raise ValueError("bad base address")
    token = str(data.get("token", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token):
        raise ValueError("bad token")
    inputs = data.get("inputs")
    if not isinstance(inputs, dict) or not KINDS[kind] <= set(inputs):
        raise ValueError("inputs missing")
    clean = {}
    for name, meta in inputs.items():
        if name not in KINDS[kind] or not isinstance(meta, dict):
            raise ValueError("unknown input")
        sha = str(meta.get("sha256", ""))
        size = meta.get("bytes")
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or not 0 < size <= MAX_INPUT[name]:
            raise ValueError("bad input record")
        clean[name] = {"sha256": sha, "bytes": size}
    return {"kind": kind, "base": base, "token": token, "inputs": clean}


def cmd_open(kind: str) -> None:
    if kind not in KINDS:
        die("unknown kind")
    try:
        blob = base64.b64decode(os.environ.get("PAYLOAD", "").strip(), validate=True)
    except Exception:
        die("payload is not base64")
    if len(blob) > 8192:
        die("payload too large")
    try:
        data = json.loads(age_decrypt(blob).decode("utf-8"))
    except SystemExit:
        raise
    except Exception:
        die("payload is not JSON")
    for v in (data.get("base"), data.get("token")) if isinstance(data, dict) else ():
        if isinstance(v, str) and v:
            print(f"::add-mask::{v}")
            host = re.sub(r"^https://", "", v)
            if host != v:
                print(f"::add-mask::{host}")
    try:
        job = check_payload(data, kind)
    except ValueError as e:
        die(f"payload rejected ({e})")
    f = job_file()
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(job, out)
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
        env.write(f"KIT_SHA={job['inputs']['kit']['sha256']}\n")
    print("payload ok")


def download(job: dict, name: str) -> bytes:
    req = urllib.request.Request(f"{job['base']}/runner/v1/input/{name}",
                                 headers={"Authorization": f"Bearer {job['token']}", "User-Agent": "trove-cam-runner"})
    limit = MAX_INPUT[name] + (1 << 20)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (https only, checked in check_payload)
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        die(f"{name}: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        die(f"{name}: download failed ({type(e).__name__})")
    if len(data) > limit:
        die(f"{name}: too large")
    return data


def cmd_fetch(name: str, dest: str, cache: str | None) -> None:
    if not INPUT_NAME.fullmatch(name):
        die("bad input name")
    job = json.loads(job_file().read_text(encoding="utf-8"))
    meta = job["inputs"].get(name)
    if not meta:
        die(f"{name}: not in this job")
    blob = None
    if cache and Path(cache).is_file():
        blob = Path(cache).read_bytes()
        plain = age_decrypt(blob)
        if hashlib.sha256(plain).hexdigest() != meta["sha256"]:
            blob = None  # stale cache entry: fetch it
        else:
            print(f"{name}: from cache")
    if blob is None:
        blob = download(job, name)
        plain = age_decrypt(blob)
        if hashlib.sha256(plain).hexdigest() != meta["sha256"] or len(plain) != meta["bytes"]:
            die(f"{name}: checksum mismatch")
        if cache:
            Path(cache).parent.mkdir(parents=True, exist_ok=True)
            Path(cache).write_bytes(blob)
        print(f"{name}: fetched ({len(plain) // 1024} KB)")
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(plain)


def main(argv: list[str]) -> None:
    if len(argv) >= 2 and argv[0] == "open":
        return cmd_open(argv[1])
    if len(argv) >= 3 and argv[0] == "fetch":
        cache = argv[argv.index("--cache") + 1] if "--cache" in argv else None
        return cmd_fetch(argv[1], argv[2], cache)
    die("usage: runner_job.py open <kind> | fetch <name> <dest> [--cache <file>]")


if __name__ == "__main__":
    main(sys.argv[1:])
