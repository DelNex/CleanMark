"""ClearMark — AI-provenance inspection (Windows / macOS / Linux).

Reports what a file's own metadata declares: Content Credentials (C2PA), EXIF,
XMP and generator tags. This is an evidence reporter, not an oracle — metadata
can be stripped by any re-encode, so absence of AI markers is never proof a file
is human-made.

exiftool is optional. Without it, a Pillow + OpenCV fallback reads fewer fields.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

# Generator names that identify AI tools, matched case-insensitively in metadata.
AI_TOOLS = [
    "dall-e", "dall·e", "openai", "sora", "midjourney", "stable diffusion",
    "stablediffusion", "automatic1111", "comfyui", "invokeai", "sdxl",
    "firefly", "adobe firefly", "gemini", "imagen", "veo", "google ai",
    "grok", "flux", "black forest labs", "leonardo.ai", "ideogram",
    "runway", "pika", "luma", "kling", "hailuo", "minimax", "dreamstudio",
    "nightcafe", "playground ai", "recraft", "krea", "freepik ai",
    "canva magic", "generative fill", "generative expand", "seedream",
    "qwen-image", "nano banana", "wan2", "hunyuan",
]

# IPTC digital-source-type values — the industry standard AI declaration.
IPTC_AI = ["trainedalgorithmicmedia", "compositesynthetic", "algorithmicmedia"]

CAMERA_TAGS = ["Make", "Model", "LensModel", "FNumber", "ExposureTime",
               "ISOSpeedRatings", "ISO", "FocalLength", "DateTimeOriginal"]

_EXIFTOOL = None


def exiftool_path() -> str | None:
    """Locate exiftool if the user has it; None means run in reduced mode."""
    global _EXIFTOOL
    if _EXIFTOOL is None:
        found = shutil.which("exiftool") or shutil.which("exiftool.exe")
        # App-local optional downloads are kept beside the virtual environment.
        if not found:
            app_local = Path(__file__).parent / "tools" / "exiftool"
            local = app_local / ("exiftool.exe" if sys.platform == "win32" else "exiftool")
            if local.is_file():
                found = str(local)
        # Preserve the legacy behavior for a tool placed beside the app.
        if not found:
            local = Path(__file__).parent / ("exiftool.exe" if sys.platform == "win32" else "exiftool")
            if local.is_file():
                found = str(local)
        _EXIFTOOL = found or ""
    return _EXIFTOOL or None


def _read_metadata(path: Path) -> dict:
    """Best-effort metadata dump: exiftool if present, else Pillow + OpenCV."""
    tool = exiftool_path()
    if tool:
        try:
            out = subprocess.run([tool, "-j", "-G", "-n", str(path)],
                                 capture_output=True, text=True, timeout=60, **_NO_WINDOW)
            return json.loads(out.stdout)[0]
        except Exception:
            pass

    meta: dict = {}
    try:
        from PIL import ExifTags, Image
        with Image.open(path) as im:
            meta["_size"] = f"{im.width}x{im.height}"
            meta["_format"] = im.format or ""
            for k, v in (im.info or {}).items():
                if k.lower() in ("icc_profile", "exif"):
                    continue
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "ignore")
                if isinstance(v, str):
                    meta[k] = v
            for tag_id, value in (im.getexif() or {}).items():
                meta[ExifTags.TAGS.get(tag_id, str(tag_id))] = value
    except Exception:
        pass

    if "_size" not in meta:
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                meta["_size"] = (f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
                                 f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
                meta["_frames"] = str(int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
            cap.release()
        except Exception:
            pass

    # ffprobe adds container tags for video when it happens to be installed
    if shutil.which("ffprobe"):
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_format", "-show_streams",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60, **_NO_WINDOW)
            data = json.loads(probe.stdout)
            for tag, val in (data.get("format", {}).get("tags") or {}).items():
                meta[f"Format:{tag}"] = val
            for i, stream in enumerate(data.get("streams", [])):
                for tag, val in (stream.get("tags") or {}).items():
                    meta[f"Stream{i}:{tag}"] = val
        except Exception:
            pass
    return meta


def _scan_raw_bytes(path: Path) -> dict:
    """Look for C2PA/JUMBF containers and generator strings in the raw file."""
    found = {"c2pa": False, "generator": None}
    try:
        with open(path, "rb") as fh:
            head = fh.read(3_000_000)  # manifests sit near the start of the file
    except OSError:
        return found

    if b"jumb" in head or b"c2pa" in head or b"urn:c2pa" in head:
        found["c2pa"] = True
        m = re.search(rb'"claim_generator[^"]*"\s*:\s*"([^"]{2,120})"', head)
        if not m:
            m = re.search(rb"c2pa\.actions.{0,400}?([A-Za-z][\w .()\-]{3,60})", head, re.S)
        if m:
            found["generator"] = m.group(1).decode("utf-8", "ignore").strip()
    return found


def analyse(path: Path) -> dict:
    """Return {verdict, confidence, summary, signals[], metadata{}}."""
    meta = _read_metadata(path)
    raw = _scan_raw_bytes(path)
    flat = {str(k): str(v) for k, v in meta.items()}

    def leaf(key: str) -> str:
        return key.split(":")[-1].strip().lower()

    boring = ("sourcefile", "exiftool", "file:", "composite:imagesize",
              "composite:megapixels")
    content_meta = {k: v for k, v in flat.items()
                    if not k.lower().startswith(boring) and not k.startswith("_")}

    haystack = " ".join(f"{k} {v}" for k, v in flat.items()).lower()

    signals: list[dict] = []
    ai_score = 0
    camera_score = 0

    # --- strongest: IPTC digital source type ---
    squashed = haystack.replace(" ", "").replace("-", "")
    for marker in IPTC_AI:
        if marker in squashed:
            signals.append({
                "level": "strong", "kind": "ai",
                "title": "IPTC digital-source-type declares synthetic media",
                "detail": "The file carries the industry-standard tag used to mark "
                          "AI-generated content (trainedAlgorithmicMedia).",
            })
            ai_score += 5
            break

    # --- Content Credentials (C2PA) ---
    if raw["c2pa"] or "c2pa" in haystack:
        gen = raw["generator"]
        signals.append({
            "level": "strong", "kind": "info",
            "title": "Content Credentials (C2PA) manifest present",
            "detail": ("Signed provenance data is embedded" +
                       (f", generator: “{gen}”" if gen else "") +
                       ". Inspect it at contentcredentials.org/verify for the full "
                       "signed history."),
        })
        ai_score += 2
        if gen and any(t in gen.lower() for t in AI_TOOLS):
            ai_score += 3

    # --- generator / software names ---
    hits = sorted({t for t in AI_TOOLS if t in haystack})
    if hits:
        where = [f"{k}: {v}" for k, v in flat.items()
                 if any(t in f"{k} {v}".lower() for t in hits)][:4]
        signals.append({
            "level": "strong", "kind": "ai",
            "title": f"AI generator named in metadata: {', '.join(hits[:4])}",
            "detail": ("Found in — " + " · ".join(where)) if where else "",
        })
        ai_score += 4

    # --- diffusion-tool generation parameters (A1111 / ComfyUI PNG chunks) ---
    for key in ("parameters", "prompt", "workflow", "negative prompt", "sd-metadata"):
        if key in {leaf(k) for k in flat}:
            val = next(v for k, v in flat.items() if leaf(k) == key)
            signals.append({
                "level": "strong", "kind": "ai",
                "title": f"Diffusion generation data embedded (“{key}” block)",
                "detail": "Preview: " + val[:180] + ("…" if len(val) > 180 else ""),
            })
            ai_score += 5
            break

    # --- camera capture evidence ---
    cam_present = [t for t in CAMERA_TAGS if any(t.lower() == leaf(k) for k in flat)]
    if len(cam_present) >= 3:
        make = next((v for k, v in flat.items() if leaf(k) == "make"), "")
        model = next((v for k, v in flat.items() if leaf(k) == "model"), "")
        signals.append({
            "level": "medium", "kind": "camera",
            "title": "Camera capture metadata present",
            "detail": f"Optical-capture EXIF found ({', '.join(cam_present[:5])})"
                      + (f" — device: {make} {model}".rstrip() if make or model else ""),
        })
        camera_score += 3

    # --- editing software (neither proves nor disproves AI) ---
    for editor in ("photoshop", "lightroom", "gimp", "affinity", "capture one",
                   "snapseed", "vsco", "pixelmator"):
        if editor in haystack:
            signals.append({
                "level": "weak", "kind": "info",
                "title": f"Edited with {editor.title()}",
                "detail": "The file passed through an image editor, which can also "
                          "add or remove AI markers.",
            })
            break

    # --- typical model output dimensions (weak hint only) ---
    size = flat.get("_size") or ""
    ai_sizes = {"512x512", "768x768", "1024x1024", "1024x1792", "1792x1024",
                "1216x832", "832x1216", "1344x768", "1152x896", "2048x2048"}
    if size in ai_sizes:
        signals.append({
            "level": "weak", "kind": "ai",
            "title": f"Dimensions {size} match a common model output size",
            "detail": "Many image models emit these exact sizes. On its own this "
                      "proves nothing — cropped photos can match too.",
        })
        ai_score += 1

    # --- no metadata at all (only worth saying when nothing else turned up) ---
    if len(content_meta) <= 4 and ai_score == 0 and camera_score == 0:
        signals.append({
            "level": "weak", "kind": "info",
            "title": "File carries almost no metadata",
            "detail": "Screenshots, social-media re-uploads and re-encodes strip "
                      "metadata. This makes provenance unverifiable either way.",
        })

    # --- verdict ---
    if ai_score >= 5:
        verdict, confidence = "AI-generated", "high"
        summary = "The file's own metadata declares it was produced by an AI tool."
    elif ai_score >= 3:
        verdict, confidence = "Likely AI-generated", "medium"
        summary = "Metadata points to an AI generator, but the evidence is partial."
    elif camera_score >= 3 and ai_score == 0:
        verdict, confidence = "Likely a camera capture", "medium"
        summary = ("Optical-capture metadata is present and no AI markers were found. "
                   "Note that camera EXIF can be forged or copied.")
    else:
        verdict, confidence = "Inconclusive", "low"
        summary = ("No AI provenance data and no camera evidence found. This is the "
                   "normal result for screenshots and re-uploaded files — it does "
                   "not mean the content is authentic.")

    interesting = {k: (v[:220] + "…" if len(v) > 220 else v)
                   for k, v in content_meta.items()}

    return {
        "verdict": verdict,
        "confidence": confidence,
        "summary": summary,
        "signals": signals,
        "metadata": dict(list(interesting.items())[:60]),
        "metadata_count": len(interesting),
        "reduced_mode": exiftool_path() is None,
    }


if __name__ == "__main__":
    print(json.dumps(analyse(Path(sys.argv[1])), indent=2))
