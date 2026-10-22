"""ClearMark local HTTP backend, hosted inside the desktop window."""
import platform
import secrets
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, request, send_file

from detector import analyse, exiftool_path
from remover import IMAGE_EXTS, VIDEO_EXTS, JobCancelled, remove_watermark

_CPU_NAME = None
_CPU_LOCK = threading.Lock()


def _cpu_name() -> str:
    """Return a cached CPU description; never block the request on PowerShell."""
    global _CPU_NAME
    if _CPU_NAME is not None:
        return _CPU_NAME

    def discover():
        global _CPU_NAME
        try:
            if sys.platform == "darwin":
                out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                     capture_output=True, text=True, timeout=5)
                name = out.stdout.strip()
            elif sys.platform == "win32":
                name = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Processor).Name"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                ).stdout.strip()
            else:
                name = next((line.split(":", 1)[1].strip()
                             for line in Path("/proc/cpuinfo").read_text().splitlines()
                             if line.lower().startswith("model name")), "")
            _CPU_NAME = name.splitlines()[0].strip() if name else (
                platform.processor() or platform.machine() or "Unknown CPU")
        except Exception:
            _CPU_NAME = platform.processor() or platform.machine() or "Unknown CPU"

    if _CPU_LOCK.acquire(blocking=False):
        def safe_discover():
            try:
                discover()
            finally:
                _CPU_LOCK.release()
        threading.Thread(target=safe_discover, daemon=True).start()
    return "Detecting CPU…"


def create_app(work_dir: Path | str | None = None, auth_token: str | None = None) -> Flask:
    """Create an isolated Flask app and its private job/work state."""
    root = Path(__file__).resolve().parent
    static_dir = root / "static"
    app = Flask(__name__, static_folder=str(static_dir))
    app.config["WORK_DIR"] = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "clearmark"
    app.config["AUTH_TOKEN"] = auth_token or secrets.token_urlsafe(32)
    app.config["JOBS"] = {}
    app.config["JOBS_LOCK"] = threading.RLock()
    work = app.config["WORK_DIR"]
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.iterdir():
        try:
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
            elif stale.is_dir():
                import shutil
                shutil.rmtree(stale)
        except OSError:
            pass

    @app.before_request
    def require_local_auth():
        if request.method == "POST" or request.path.startswith("/result/"):
            if not secrets.compare_digest(request.headers.get("X-ClearMark-Token", ""),
                                          app.config["AUTH_TOKEN"]):
                abort(403)

    @app.get("/")
    def index():
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        html = html.replace("<!-- CLEARMARK_TOKEN -->", app.config["AUTH_TOKEN"])
        return html

    @app.get("/_health")
    def health():
        return {"ok": True}

    @app.get("/info")
    def info():
        import os
        gpu_status = "torch_error"
        try:
            import torch
            if torch.cuda.is_available():
                device, accel = "cuda", torch.cuda.get_device_name(0)
                gpu_status = "available"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device, accel = "mps", ""
                gpu_status = "available"
            else:
                device, accel = "cpu", ""
                gpu_status = "cpu_build" if torch.version.cuda is None else "cuda_unavailable"
        except Exception:
            device, accel = "cpu", ""
        return {"device": device, "accelerator": accel, "chip": _cpu_name(),
                "cores": os.cpu_count(), "os": platform.system(),
                "exiftool": exiftool_path() is not None, "gpu_status": gpu_status}

    def run_job(job_id: str, src: Path, dst: Path, box):
        jobs = app.config["JOBS"]
        with app.config["JOBS_LOCK"]:
            job = jobs[job_id]

        def progress(done, total):
            with app.config["JOBS_LOCK"]:
                job["done"] = done
                if total:
                    job["total"] = total

        try:
            remove_watermark(src, dst, box, progress=progress, cancel_event=job["cancel"])
            with app.config["JOBS_LOCK"]:
                job["status"] = "done"
        except JobCancelled:
            with app.config["JOBS_LOCK"]:
                job["status"] = "cancelled"
        except Exception as exc:
            with app.config["JOBS_LOCK"]:
                job["status"], job["error"] = "error", str(exc)
            dst.unlink(missing_ok=True)
        finally:
            src.unlink(missing_ok=True)

    @app.post("/process")
    def process():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return {"error": "No file uploaded"}, 400
        ext = Path(upload.filename).suffix.lower()
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            return {"error": f"Unsupported file type: {ext}"}, 400
        try:
            box = tuple(int(float(request.form[k])) for k in ("x", "y", "w", "h"))
        except (KeyError, ValueError):
            box = None
        job_id = uuid.uuid4().hex[:12]
        src = work / f"{job_id}_in{ext}"
        out_ext = ".png" if ext in IMAGE_EXTS else ".mp4"
        dst = work / f"{job_id}_out{out_ext}"
        upload.save(src)
        with app.config["JOBS_LOCK"]:
            # Completed results remain available until saved or the app closes.
            # Bound stale non-running jobs so repeated scans cannot grow forever.
            jobs = app.config["JOBS"]
            if len(jobs) > 50:
                for old_id, old in list(jobs.items()):
                    if old["status"] != "running":
                        old["dst"].unlink(missing_ok=True)
                        jobs.pop(old_id, None)
                    if len(jobs) <= 40:
                        break
            app.config["JOBS"][job_id] = {
                "status": "running", "done": 0, "total": None, "error": None,
                "dst": dst, "name": Path(upload.filename).stem + "_clean" + out_ext,
                "cancel": threading.Event()
            }
            job = app.config["JOBS"][job_id]
        threading.Thread(target=run_job, args=(job_id, src, dst, box), daemon=True).start()
        return {"job": job_id, "video": ext in VIDEO_EXTS}

    @app.post("/detect")
    def detect():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return {"error": "No file uploaded"}, 400
        ext = Path(upload.filename).suffix.lower()
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            return {"error": f"Unsupported file type: {ext}"}, 400
        tmp = work / f"scan_{uuid.uuid4().hex[:12]}{ext}"
        upload.save(tmp)
        try:
            report = analyse(tmp)
        except Exception as exc:
            return {"error": str(exc)}, 500
        finally:
            tmp.unlink(missing_ok=True)
        report["filename"] = upload.filename
        return report

    @app.get("/progress/<job_id>")
    def progress(job_id):
        with app.config["JOBS_LOCK"]:
            job = app.config["JOBS"].get(job_id)
            if job is None:
                return {"error": "Unknown job"}, 404
            return {"status": job["status"], "done": job["done"],
                    "total": job["total"], "error": job["error"]}

    @app.post("/cancel/<job_id>")
    def cancel(job_id):
        with app.config["JOBS_LOCK"]:
            job = app.config["JOBS"].get(job_id)
            if job is None:
                return {"error": "Unknown job"}, 404
            job["cancel"].set()
        return {"ok": True}

    @app.get("/result/<job_id>")
    def result(job_id):
        with app.config["JOBS_LOCK"]:
            job = app.config["JOBS"].get(job_id)
            if job is None or job["status"] != "done":
                return {"error": "Not ready"}, 404
            return send_file(job["dst"], as_attachment=False, download_name=job["name"])

    return app


if __name__ == "__main__":
    app = create_app()
    print("ClearMark backend is for desktop.py; use start-linux.sh for source development.")
    app.run(host="127.0.0.1", port=5177, debug=False, threaded=True)
