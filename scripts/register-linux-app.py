#!/usr/bin/env python3
"""Install or remove ClearMark's per-user Linux application-menu entry."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def desktop_quote(value: str) -> str:
    """Quote one argument according to the Desktop Entry Exec value rules."""
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("$", "\\$").replace("`", "\\`").replace("%", "%%")
    return f'"{value}"'


def desktop_string(value: str) -> str:
    """Escape a Desktop Entry string value (not an Exec argument)."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: register-linux-app.py APP_DIRECTORY PYTHON_EXECUTABLE", file=sys.stderr)
        return 2

    app_dir = Path(sys.argv[1]).expanduser().resolve()
    python = Path(sys.argv[2]).expanduser().resolve()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    applications = data_home / "applications"
    entry = applications / "clearmark.desktop"

    if not (app_dir / "desktop.py").is_file() or not python.is_file():
        print("ClearMark is not fully set up yet; skipping app-menu registration.", file=sys.stderr)
        return 1

    icon = app_dir / "static" / "clearmark.svg"
    contents = "\n".join(
        [
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            "Name=ClearMark",
            "GenericName=AI Watermark Remover",
            "Comment=Remove watermarks from images and videos on this computer",
            f"Exec={desktop_quote(str(python))} {desktop_quote(str(app_dir / 'desktop.py'))}",
            f"TryExec={desktop_string(str(python))}",
            f"Path={desktop_string(str(app_dir))}",
            f"Icon={desktop_string(str(icon))}",
            "Terminal=false",
            "StartupNotify=true",
            "Categories=Graphics;",
            "Keywords=watermark;image;video;AI;",
            "",
        ]
    )

    applications.mkdir(parents=True, exist_ok=True)
    temporary = entry.with_suffix(".desktop.tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.replace(entry)
    entry.chmod(0o644)

    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(applications)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"ClearMark is available in your Linux app search: {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
