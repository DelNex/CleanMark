#!/usr/bin/env bash
# Set up and launch ClearMark on Linux. Python and uv remain inside this folder.
set -o pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

echo ""
echo "  =========================================="
echo "   ClearMark - AI Watermark Remover"
echo "   by DelNex"
echo "  =========================================="
echo ""

RUNTIME_DIR="$APP_DIR/.runtime"
UV_DIR="$RUNTIME_DIR/bin"
UV_BIN="$UV_DIR/uv"
export UV_INSTALL_DIR="$UV_DIR"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
export UV_PYTHON_INSTALL_BIN=0

pause_if_interactive() {
  if [ -t 0 ]; then read -r -p "  Press Return to close." _; fi
}

fail() {
  echo ""
  echo "  $1"
  pause_if_interactive
  exit 1
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  elif [ -x "$UV_DIR/uv" ]; then
    UV_BIN="$UV_DIR/uv"
  else
    mkdir -p "$UV_DIR" || return 1
    echo "  Downloading the small uv setup tool..."
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh |
        env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 sh || return 1
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh |
        env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 sh || return 1
    else
      echo "  Install curl or wget so ClearMark can download its private Python runtime."
      return 1
    fi
    [ -x "$UV_DIR/uv" ] || return 1
    UV_BIN="$UV_DIR/uv"
  fi
}

desktop_stack_ok() {
  command -v pkg-config >/dev/null 2>&1 &&
    pkg-config --exists gtk+-3.0 &&
    { pkg-config --exists webkit2gtk-4.1 || pkg-config --exists webkit2gtk-4.0; }
}

install_desktop_stack() {
  local -a privilege=()
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || return 1
    privilege=(sudo)
  fi
  [ -r /etc/os-release ] && . /etc/os-release
  case "${ID:-}" in
    arch|endeavouros|manjaro)
      "${privilege[@]}" pacman -S --needed gtk3 webkit2gtk-4.1 gobject-introspection cairo pkgconf gcc make
      ;;
    ubuntu|debian|linuxmint|pop)
      local webkit_dev=libwebkit2gtk-4.1-dev gi_dev=libgirepository-2.0-dev
      apt-cache show "$webkit_dev" >/dev/null 2>&1 || webkit_dev=libwebkit2gtk-4.0-dev
      apt-cache show "$gi_dev" >/dev/null 2>&1 || gi_dev=libgirepository1.0-dev
      "${privilege[@]}" apt-get update &&
        "${privilege[@]}" apt-get install -y "$webkit_dev" "$gi_dev" libgtk-3-dev libcairo2-dev libglib2.0-dev pkg-config build-essential
      ;;
    fedora)
      "${privilege[@]}" dnf install -y gtk3-devel webkit2gtk4.1-devel gobject-introspection-devel cairo-devel pkgconf-pkg-config gcc make
      ;;
    opensuse*|suse)
      "${privilege[@]}" zypper --non-interactive install gtk3-devel webkit2gtk3-devel gobject-introspection-devel cairo-devel pkg-config gcc make
      ;;
    *)
      echo "  Automatic Linux desktop-library setup is not configured for ${ID:-this distribution}."
      echo "  Install GTK 3 and WebKitGTK 4.0 or 4.1 development packages, then retry."
      return 1
      ;;
  esac
}

if ! desktop_stack_ok; then
  echo "  GTK/WebKitGTK development packages for the native window are missing."
  read -r -p "  Install them with your Linux package manager? [y/N] " answer
  case "$answer" in
    [yY]|[yY][eE][sS])
      install_desktop_stack || fail "Could not install the GTK/WebKitGTK packages."
      desktop_stack_ok || fail "GTK/WebKitGTK is still unavailable after installation."
      ;;
    *) fail "ClearMark's native Linux window needs GTK 3 and WebKitGTK 4.0 or 4.1." ;;
  esac
fi

VENV_PY="$APP_DIR/.venv/bin/python"
USE_UV=0
if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
  USE_UV=1
elif [ -x "$UV_DIR/uv" ]; then
  UV_BIN="$UV_DIR/uv"
  USE_UV=1
fi

# Reuse an existing working environment. This also lets the app start when
# system Python has since been removed.
if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
  :
else
  if [ -e .venv ]; then rm -rf .venv; fi
  PY=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      PY="$(command -v "$candidate")"
      break
    fi
  done

  if [ -n "$PY" ]; then
    echo "  Creating the local Python environment with $PY..."
    if ! "$PY" -m venv .venv; then
      echo "  The system Python cannot create a virtual environment; using managed Python instead."
      rm -rf .venv
      PY=""
    fi
  fi

  if [ -z "$PY" ]; then
    echo "  No usable Python 3.10+ was found. ClearMark can install one locally."
    find_uv || fail "Could not download uv. Check your internet connection and try again."
    echo "  Downloading a private Python 3.12 runtime..."
    "$UV_BIN" python install 3.12 || fail "Could not download Python 3.12."
    "$UV_BIN" venv --python 3.12 .venv || fail "Could not create the local Python environment."
    USE_UV=1
  fi
fi

if [ ! -x "$VENV_PY" ]; then
  fail "The local Python environment was not created."
fi

echo "  Installing required packages..."
if [ "$USE_UV" = 1 ]; then
  "$UV_BIN" pip install --python "$VENV_PY" -r requirements.txt ||
    fail "Could not install ClearMark's required packages. Check your internet connection."
else
  "$VENV_PY" -m pip install -r requirements.txt ||
    fail "Could not install ClearMark's required packages. Check your internet connection."
fi

if ! "$VENV_PY" -c 'import torch; from pathlib import Path; raise SystemExit(0 if (Path(torch.hub.get_dir()) / "checkpoints" / "big-lama.pt").is_file() else 1)' >/dev/null 2>&1; then
  echo ""
  echo "  Downloading the LaMa AI model (~200 MB, one time)..."
  "$VENV_PY" -c "import remover; remover._get_lama()" ||
    fail "Could not download the AI model. Check your connection and run this launcher again."
fi

OPTIONAL_SKIP="$RUNTIME_DIR/exiftool-skipped"
if [ ! -e "$OPTIONAL_SKIP" ] &&
   ! "$VENV_PY" -c 'from detector import exiftool_path; raise SystemExit(0 if exiftool_path() else 1)' >/dev/null 2>&1; then
  echo ""
  read -r -p "  Install optional ExifTool for richer metadata checks? [y/N] " answer
  case "$answer" in
    [yY]|[yY][eE][sS])
      if ! "$VENV_PY" install_optional.py exiftool; then
        echo "  ExifTool could not be installed; metadata checks will use reduced mode."
      fi
      ;;
    *)
      mkdir -p "$RUNTIME_DIR"
      touch "$OPTIONAL_SKIP"
      echo "  Skipping optional ExifTool."
      ;;
  esac
fi

echo "  Adding ClearMark to Linux app search..."
"$VENV_PY" register-linux-app.py "$APP_DIR" "$VENV_PY" ||
  echo "  App search registration failed; ClearMark can still start here."

echo "  Starting the ClearMark desktop window."
echo ""
exec "$VENV_PY" desktop.py
