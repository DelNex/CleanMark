"""ClearMark — watermark removal engine (Windows / macOS / Linux).

Images and video frames are inpainted with the LaMa deep-learning model, which
reconstructs real texture and structure instead of blurring. Runs on an NVIDIA
GPU (CUDA) or Apple GPU (MPS) when available, otherwise a multi-threaded CPU pool.

Everything happens locally — no network access is used after the first-run model
download.
"""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

_LAMA = None
_FFMPEG = None
_LAMA_URL = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"

# Hide the console window ffmpeg would otherwise flash on Windows.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class JobCancelled(Exception):
    """Raised when the user aborts a running job."""


def ffmpeg_exe() -> str:
    """Path to an ffmpeg binary: the pip-installed one first, then any on PATH."""
    global _FFMPEG
    if _FFMPEG is None:
        try:
            import imageio_ffmpeg
            _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            _FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
    return _FFMPEG


class _Lama:
    """big-lama TorchScript model on the best available device."""

    def __init__(self):
        import torch
        self.torch = torch
        if torch.cuda.is_available():
            self.device = "cuda"
            self.device_name = torch.cuda.get_device_name(0)
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            self.device = "mps"
            self.device_name = "Apple GPU"
        else:
            self.device = "cpu"
            self.device_name = "CPU"

        cache = Path(torch.hub.get_dir()) / "checkpoints" / "big-lama.pt"
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            print("Downloading the LaMa model (~200 MB, one time only)…", flush=True)
            torch.hub.download_url_to_file(_LAMA_URL, str(cache))
        self.model = torch.jit.load(str(cache), map_location="cpu").eval().to(self.device)
        print(f"LaMa model loaded on {self.device} ({self.device_name})", flush=True)

    def __call__(self, img_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """img_rgb HxWx3 uint8, mask HxW uint8 -> inpainted HxWx3 uint8 RGB."""
        torch = self.torch
        h, w = img_rgb.shape[:2]
        ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
        if ph or pw:
            img_rgb = np.pad(img_rgb, ((0, ph), (0, pw), (0, 0)), mode="reflect")
            mask = np.pad(mask, ((0, ph), (0, pw)), mode="reflect")
        ti = (torch.from_numpy(np.ascontiguousarray(img_rgb))
              .permute(2, 0, 1).unsqueeze(0).float().div(255).to(self.device))
        tm = (torch.from_numpy((mask > 127).astype(np.float32))
              .unsqueeze(0).unsqueeze(0).to(self.device))
        with torch.inference_mode():
            out = self.model(ti, tm)[0].permute(1, 2, 0).cpu().numpy()
        return (np.clip(out, 0, 1) * 255).astype(np.uint8)[:h, :w]


def _get_lama():
    global _LAMA
    if _LAMA is None:
        _LAMA = _Lama()
    return _LAMA


def default_box(width: int, height: int) -> tuple[int, int, int, int]:
    """Default watermark region: bottom-right corner (where Gemini/Veo put it)."""
    w = max(80, int(width * 0.20))
    h = max(40, int(height * 0.09))
    return (width - w - int(width * 0.02), height - h - int(height * 0.02), w, h)


def clamp_box(x: int, y: int, w: int, h: int, width: int, height: int):
    x = max(0, min(x, width - 2))
    y = max(0, min(y, height - 2))
    w = max(2, min(w, width - x))
    h = max(2, min(h, height - y))
    return x, y, w, h


def _inpaint_patch(img: np.ndarray, box: tuple[int, int, int, int]) -> None:
    """AI-inpaint the box region of a BGR image in place.

    Only a context window around the box goes through the model — much faster
    than full-frame inference and identical quality for a corner logo.
    """
    height, width = img.shape[:2]
    x, y, w, h = clamp_box(*box, width, height)

    pad = max(96, int(max(w, h) * 1.5))
    cx0, cy0 = max(0, x - pad), max(0, y - pad)
    cx1, cy1 = min(width, x + w + pad), min(height, y + h + pad)
    crop = img[cy0:cy1, cx0:cx1]

    mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    mask[y - cy0: y - cy0 + h, x - cx0: x - cx0 + w] = 255
    # Grow the mask so anti-aliased watermark edges are covered too.
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)

    try:
        result = _get_lama()(crop[:, :, ::-1], mask)[:, :, ::-1]
    except Exception:
        result = cv2.inpaint(crop, mask, inpaintRadius=4, flags=cv2.INPAINT_TELEA)

    keep = mask > 0
    crop[keep] = result[keep]


def remove_from_image(src: Path, dst: Path, box: tuple[int, int, int, int]) -> None:
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image: {src.name}")
    _inpaint_patch(img, box)
    if not cv2.imwrite(str(dst), img):
        raise ValueError(f"Could not write image: {dst.name}")


def probe_video(src: Path) -> tuple[int, int, float, int | None]:
    """Read width, height, fps and frame count with OpenCV (no ffprobe needed)."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"Could not open video: {src.name}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if width <= 0 or height <= 0:
        raise ValueError(f"Could not read video dimensions: {src.name}")
    if not 0.1 < fps < 1000:
        fps = 30.0
    return width, height, fps, (count if count > 0 else None)


def remove_from_video(src: Path, dst: Path, box: tuple[int, int, int, int],
                      progress=None, cancel_event=None) -> None:
    """AI-inpaint every frame: decode via ffmpeg pipe -> LaMa patch -> encode.

    On CPU, frames are inpainted by a pool of worker threads (results written
    back in order). `progress(done, total)` is called per frame; setting
    `cancel_event` aborts the job and removes the partial output.
    """
    import os
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    width, height, fps, total = probe_video(src)
    box = clamp_box(*box, width, height)
    frame_bytes = width * height * 3
    ff = ffmpeg_exe()

    lama = _get_lama()  # load the model once before worker threads share it
    cores = os.cpu_count() or 4
    if lama.device in ("mps", "cuda"):
        # GPU inference is serialised anyway; extra workers only risk OOM
        workers = 1
    else:
        workers = max(1, min(4, cores // 2))
        try:
            import torch
            torch.set_num_threads(max(1, cores // workers))
        except ImportError:
            pass

    decoder = subprocess.Popen(
        [ff, "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, **_NO_WINDOW,
    )
    encoder = subprocess.Popen(
        [ff, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
         "-r", f"{fps:.6f}", "-i", "-",
         "-i", str(src),
         "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-crf", "18", "-preset", "fast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(dst)],
        stdin=subprocess.PIPE, **_NO_WINDOW,
    )

    watcher_stop = threading.Event()

    def stop_children_on_cancel():
        while not watcher_stop.wait(0.1):
            if cancel_event is not None and cancel_event.is_set():
                for child in (decoder, encoder):
                    if child.poll() is None:
                        child.kill()
                return

    watcher = threading.Thread(target=stop_children_on_cancel, daemon=True)
    watcher.start()

    def patch(frame: np.ndarray) -> np.ndarray:
        _inpaint_patch(frame, box)
        return frame

    n = 0
    cancelled = False
    try:
        with ThreadPoolExecutor(workers) as pool:
            pending: deque = deque()
            eof = False
            while not eof or pending:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                while not eof and len(pending) < workers * 2:
                    raw = decoder.stdout.read(frame_bytes)
                    if len(raw) < frame_bytes:
                        eof = True
                        break
                    frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3).copy()
                    pending.append(pool.submit(patch, frame))
                if pending:
                    encoder.stdin.write(pending.popleft().result().tobytes())
                    n += 1
                    if progress is not None:
                        progress(n, total)
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
    finally:
        watcher_stop.set()
        watcher.join(timeout=1)
        if cancelled:
            decoder.kill()
            encoder.kill()
            decoder.wait()
            encoder.wait()
            dst.unlink(missing_ok=True)
        else:
            decoder.stdout.close()
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
            decoder.wait()
            encoder.wait()

    if cancelled:
        raise JobCancelled()
    if n == 0:
        raise RuntimeError("No frames could be read from this video.")
    if encoder.returncode != 0:
        raise RuntimeError("ffmpeg could not encode the output video.")


def remove_watermark(src: Path, dst: Path, box: tuple[int, int, int, int] | None = None,
                     progress=None, cancel_event=None) -> Path:
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled()
    ext = src.suffix.lower()
    if box is None:
        if ext in IMAGE_EXTS:
            img = cv2.imread(str(src))
            if img is None:
                raise ValueError(f"Could not read image: {src.name}")
            box = default_box(img.shape[1], img.shape[0])
        else:
            vw, vh, _, _ = probe_video(src)
            box = default_box(vw, vh)

    if ext in IMAGE_EXTS:
        remove_from_image(src, dst, box)
    elif ext in VIDEO_EXTS:
        remove_from_video(src, dst, box, progress=progress, cancel_event=cancel_event)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    if cancel_event is not None and cancel_event.is_set():
        dst.unlink(missing_ok=True)
        raise JobCancelled()
    return dst


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Remove a watermark from an image or video (local, offline).")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--box", type=str, default=None,
                        help="x,y,w,h in pixels (default: bottom-right corner)")
    args = parser.parse_args()

    out = args.output or args.input.with_name(args.input.stem + "_clean" + args.input.suffix)
    box = tuple(int(v) for v in args.box.split(",")) if args.box else None
    remove_watermark(args.input, out, box,
                     progress=lambda d, t: print(f"\r  frame {d}/{t or '?'}", end="", flush=True))
    print(f"\nSaved: {out}")
