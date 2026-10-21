"""Native ClearMark window for Windows and Linux (pywebview)."""
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview
from werkzeug.serving import make_server

from app import create_app

HOST = "127.0.0.1"
INSTANCE_PORT = 5179


class DesktopApi:
    def __init__(self, flask_app):
        # Keep implementation objects private. pywebview inspects the API
        # object for bridge members; exposing the native Window here makes
        # GTK/WebKit descriptors such as ``window.native.bin`` get probed
        # from JavaScript and produces noisy attribute errors.
        self._flask_app = flask_app
        self._window = None

    def save_file(self, job_id):
        """Open Save As and copy a completed result; remove it only on success."""
        jobs = self._flask_app.config["JOBS"]
        lock = self._flask_app.config["JOBS_LOCK"]
        with lock:
            job = jobs.get(str(job_id))
            if job is None or job["status"] != "done":
                return {"ok": False, "error": "The result is no longer available."}
            src, suggested = job["dst"], job["name"]
        choice = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=suggested,
            file_types=("Media files (*.png;*.mp4)", "All files (*.*)"))
        if not choice:
            return {"ok": False, "cancelled": True}
        destination = Path(choice[0] if isinstance(choice, (list, tuple)) else choice)
        try:
            shutil.copy2(src, destination)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        with lock:
            current = jobs.pop(str(job_id), None)
        if current:
            current["dst"].unlink(missing_ok=True)
        return {"ok": True, "path": str(destination)}


def acquire_single_instance():
    """Forward a focus request to the running app, or own the local socket."""
    listener = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((HOST, INSTANCE_PORT))
        listener.listen(4)
        listener.settimeout(1)
        return listener
    except OSError as bind_error:
        if listener is not None:
            listener.close()
        try:
            with socket.create_connection((HOST, INSTANCE_PORT), timeout=1) as client:
                client.sendall(b"FOCUS\n")
        except OSError:
            raise RuntimeError(
                f"Could not acquire the ClearMark single-instance lock on {HOST}:{INSTANCE_PORT}"
            ) from bind_error
        return None


def main():
    instance = acquire_single_instance()
    if instance is None:
        return
    app = create_app()
    server = make_server(HOST, 0, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_port
    api = DesktopApi(app)
    window = webview.create_window("ClearMark", f"http://{HOST}:{port}/",
                                   js_api=api, min_size=(900, 650), width=1200, height=850)
    api._window = window
    stop = threading.Event()

    def focus_listener():
        while not stop.is_set():
            try:
                conn, _ = instance.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                if conn.recv(32).startswith(b"FOCUS"):
                    try:
                        window.restore()
                        window.show()
                    except Exception:
                        pass

    focus_thread = threading.Thread(target=focus_listener, daemon=True)
    focus_thread.start()

    def shutdown():
        stop.set()
        with app.config["JOBS_LOCK"]:
            for job in app.config["JOBS"].values():
                if job["status"] == "running":
                    job["cancel"].set()
                else:
                    job["dst"].unlink(missing_ok=True)
        server.shutdown()
        server.server_close()
        instance.close()

    window.events.closed += shutdown
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"http://{HOST}:{port}/_health")
            with urllib.request.urlopen(req, timeout=1):
                break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        shutdown()
        raise RuntimeError("ClearMark backend did not start in time")
    webview.start(debug=False)


if __name__ == "__main__":
    main()
