"""Download optional app-local tools selected by the user during setup."""
from __future__ import annotations

import html.parser
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

BASE_DIR = Path(__file__).resolve().parent
EXIFTOOL_PAGE = "https://exiftool.org/"


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.hrefs.extend(value for key, value in attrs if key.lower() == "href" and value)


def download_latest_link(windows: bool) -> tuple[str, str]:
    request = urllib.request.Request(
        EXIFTOOL_PAGE, headers={"User-Agent": "ClearMark optional dependency setup"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")

    links = Links()
    links.feed(page)
    pattern = (r"exiftool-([0-9]+(?:\.[0-9]+)*)_64\.zip" if windows
               else r"Image-ExifTool-([0-9]+(?:\.[0-9]+)*)\.tar\.gz")
    matches = []
    for href in links.hrefs:
        name = Path(urllib.parse.urlsplit(href).path).name
        match = re.fullmatch(pattern, name, flags=re.IGNORECASE)
        if match:
            matches.append((tuple(int(part) for part in match.group(1).split(".")), href))
    if not matches:
        raise RuntimeError("Could not find the current ExifTool download on its official site.")
    _, href = max(matches)
    return urllib.parse.urljoin(EXIFTOOL_PAGE, href), Path(urllib.parse.urlsplit(href).path).name


def safe_target(root: Path, archive_name: str) -> Path:
    member = PurePosixPath(archive_name.replace("\\", "/"))
    if member.is_absolute() or any(part in ("", ".", "..") for part in member.parts):
        raise RuntimeError(f"Unsafe path in ExifTool archive: {archive_name}")
    target = root.joinpath(*member.parts)
    if root.resolve() not in target.resolve().parents:
        raise RuntimeError(f"Unsafe path in ExifTool archive: {archive_name}")
    return target


def extract_zip_safely(archive: Path, root: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = safe_target(root, info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("ExifTool archive contains an unexpected symbolic link.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def extract_tar_safely(archive: Path, root: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = safe_target(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Could not read ExifTool archive member: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise RuntimeError("ExifTool archive contains an unexpected link or special file.")


def install_exiftool() -> Path:
    windows = sys.platform == "win32"
    if not windows and shutil.which("perl") is None:
        raise RuntimeError("ExifTool needs Perl on Linux. Install Perl, then run ClearMark setup again.")

    url, filename = download_latest_link(windows)
    tools_dir = BASE_DIR / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clearmark-exiftool-") as temporary:
        temp = Path(temporary)
        archive = temp / filename
        request = urllib.request.Request(url, headers={"User-Agent": "ClearMark optional dependency setup"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)

        unpacked = temp / "unpacked"
        unpacked.mkdir()
        if windows:
            extract_zip_safely(archive, unpacked)
            executables = list(unpacked.rglob("exiftool(-k).exe")) + list(unpacked.rglob("exiftool.exe"))
            if not executables:
                raise RuntimeError("The downloaded Windows archive did not contain ExifTool.")
            source_dir = executables[0].parent
            staged = tools_dir / ".exiftool-staged"
            shutil.rmtree(staged, ignore_errors=True)
            shutil.copytree(source_dir, staged)
            old_name = staged / "exiftool(-k).exe"
            if old_name.exists():
                old_name.replace(staged / "exiftool.exe")
            executable = staged / "exiftool.exe"
        else:
            extract_tar_safely(archive, unpacked)
            sources = list(unpacked.glob("Image-ExifTool-*/exiftool"))
            if not sources:
                raise RuntimeError("The downloaded Linux archive did not contain ExifTool.")
            source_dir = sources[0].parent
            staged = tools_dir / ".exiftool-staged"
            shutil.rmtree(staged, ignore_errors=True)
            shutil.copytree(source_dir, staged)
            executable = staged / "exiftool"
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        destination = tools_dir / "exiftool"
        previous = tools_dir / ".exiftool-previous"
        shutil.rmtree(previous, ignore_errors=True)
        if destination.exists():
            destination.replace(previous)
        try:
            staged.replace(destination)
        except Exception:
            if previous.exists() and not destination.exists():
                previous.replace(destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)

    return destination / ("exiftool.exe" if windows else "exiftool")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].lower() != "exiftool":
        print("usage: install_optional.py exiftool", file=sys.stderr)
        return 2
    try:
        path = install_exiftool()
        print(f"Optional ExifTool is ready: {path}")
        return 0
    except Exception as exc:
        print(f"Could not install optional ExifTool: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
