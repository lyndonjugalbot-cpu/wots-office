"""Convert Mixamo downloads (assets/mixamo/) into small web assets for the 3D office.

  .venv/bin/python scripts/mixamo/convert_mixamo.py

characters/*.fbx -> frontend/public/characters/{name}.glb  (skinned mesh; WebP textures <= 1024 px and
                    meshopt-compressed geometry via gltf-transform: ~1 MB instead of ~50 MB)
animations/*.fbx -> frontend/public/characters/animations.json  (motion only, no skin; rounded, and long
                    clips trimmed, so the file stays small)

Uses three.js's own FBXLoader and GLTFExporter inside headless Chromium (Playwright), the same
loader family the dashboard uses. Mixamo's licence allows using these inside our app, but not
sharing the raw files: assets/mixamo/ and the converted models stay out of git.
"""
from __future__ import annotations

import base64
import functools
import http.server
import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "assets" / "mixamo"
OUT = ROOT / "frontend" / "public" / "characters"
MAX_TEXTURE = 1024
MAX_CLIP_SECONDS = 15  # long loops (e.g. Sitting Talking, 44 s) don't need their whole length
GLTF_TRANSFORM = ["npx", "--yes", "@gltf-transform/cli@4", "optimize"]
OPTIMIZE_FLAGS = ["--compress", "meshopt", "--texture-compress", "webp", "--texture-size", str(MAX_TEXTURE),
                  "--simplify", "false", "--flatten", "false", "--join", "false"]


def compress(raw: bytes, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.glb"
        src.write_bytes(raw)
        subprocess.run([*GLTF_TRANSFORM, str(src), str(target), *OPTIMIZE_FLAGS], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def slim(clip: dict) -> dict:
    """Trim long clips and round keyframes to 4 decimals (invisible, and ~half the size)."""
    for track in clip["tracks"]:
        times, values = track["times"], track["values"]
        stride = len(values) // max(1, len(times))
        keep = [i for i, t in enumerate(times) if t <= MAX_CLIP_SECONDS] or [0]
        track["times"] = [round(times[i], 4) for i in keep]
        track["values"] = [round(v, 4) for i in keep for v in values[i * stride:(i + 1) * stride]]
    clip["duration"] = min(clip["duration"], MAX_CLIP_SECONDS)
    return clip
# Mixamo animation names -> what the office uses them for
CLIP_NAMES = {
    "typing": "typing", "sitting": "sit_idle", "sitting idle": "sit_idle", "stand to sit": "sit_down",
    "sit to stand": "stand_up", "walking": "walk", "breathing idle": "idle", "idle": "idle",
    "standing idle": "idle", "drinking": "drink", "sitting thumbs up": "sit_done",
    "sitting disapproval": "sit_error", "sitting talking": "sit_waiting",
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None  # quiet
    handler = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a, **k: None})
    handler = functools.partial(handler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    manifest = {"characters": [], "clips": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--js-flags=--max-old-space-size=8192"])
        page = browser.new_page()
        page.on("console", lambda m: print("  [browser]", m.text) if m.type in {"error", "warning"} else None)
        page.goto(f"{base}/scripts/mixamo/convert.html")
        page.wait_for_function("window.ready === true")
        page.set_default_timeout(600_000)

        for fbx in sorted((SRC / "characters").glob("*.fbx")):
            name = slug(fbx.stem.replace("_nonPBR", ""))
            print(f"character {fbx.name} ...", flush=True)
            r = page.evaluate("([u, m]) => convertCharacter(u, m)",
                              [f"{base}/assets/mixamo/characters/{fbx.name}", MAX_TEXTURE])
            raw = base64.b64decode(r["b64"])
            compress(raw, OUT / f"{name}.glb")
            data = (OUT / f"{name}.glb").read_bytes()
            manifest["characters"].append({"id": name, "file": f"{name}.glb", "height": r["height"]})
            print(f"  -> {name}.glb {len(data) / 1e6:.1f} MB, height {r['height']:.1f}, {r['meshes']} meshes, "
                  f"{int(r['triangles'])} tris, {r['bones']} bones, {r['brokenTextures']} unloadable texture(s)")
            for m in r["materials"]:
                print("     ", m)

        clips = []
        for fbx in sorted((SRC / "animations").glob("*.fbx")):
            key = CLIP_NAMES.get(fbx.stem.lower().strip())
            if not key:
                print(f"skip animation {fbx.name} (not used)")
                continue
            r = page.evaluate("([u, n]) => extractClip(u, n)", [f"{base}/assets/mixamo/animations/{fbx.name}", key])
            clips.append(slim(r["json"]))
            manifest["clips"][key] = {"source": fbx.name, "duration": round(r["duration"], 2), "tracks": r["tracks"]}
            print(f"clip {key:12} <- {fbx.name}: {r['duration']:.1f}s, {r['tracks']} tracks")
        browser.close()
    (OUT / "animations.json").write_text(json.dumps(clips, separators=(",", ":")))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    missing = sorted(set(CLIP_NAMES.values()) - set(manifest["clips"]))
    print(f"animations.json {(OUT / 'animations.json').stat().st_size / 1e6:.1f} MB; missing clips: {missing or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
