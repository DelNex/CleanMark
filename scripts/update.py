"""Check GitHub for ClearMark updates and apply fast-forward updates safely."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

APP_DIR = Path(__file__).resolve().parent.parent
REPOSITORY = "DelNex/CleanMark"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPOSITORY}"
USER_AGENT = "ClearMark-Updater"


def request(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.read()


def local_git_update() -> bool | None:
    """Return None for archives; for Git checkouts, fast-forward only when safe."""
    if not (APP_DIR / ".git").exists():
        return None
    if not shutil.which("git"):
        print("ClearMark updater: Git is unavailable; automatic update skipped.")
        return False

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=APP_DIR, capture_output=True, text=True, timeout=20
        )

    remote = git("remote", "get-url", "origin")
    normalized_remote = remote.stdout.strip().lower().replace(".git", "").rstrip("/").replace(":", "/")
    if remote.returncode or "github.com/delnex/cleanmark" not in normalized_remote:
        print("ClearMark updater: automatic update skipped (origin is not DelNex/CleanMark).")
        return False
    branch = git("branch", "--show-current")
    if branch.returncode or branch.stdout.strip() != BRANCH:
        print("ClearMark updater: automatic update skipped (not on main).")
        return False
    dirty = git("status", "--porcelain")
    if dirty.returncode or dirty.stdout.strip():
        print("ClearMark updater: update skipped because local files have changes.")
        return False
    fetch = git("fetch", "--quiet", "origin", BRANCH)
    if fetch.returncode:
        raise RuntimeError(fetch.stderr.strip() or "Git fetch failed")
    behind = git("rev-list", "--count", f"HEAD..origin/{BRANCH}")
    if behind.returncode:
        raise RuntimeError(behind.stderr.strip() or "Could not compare GitHub revisions")
    if behind.stdout.strip() == "0":
        return False
    merge = git("merge", "--ff-only", f"origin/{BRANCH}")
    if merge.returncode:
        raise RuntimeError(merge.stderr.strip() or "Could not fast-forward to GitHub")
    print("ClearMark updated from GitHub.")
    return True


def safe_member_path(root: Path, name: str) -> Path:
    member = PurePosixPath(name)
    if member.is_absolute() or any(part in ("", ".", "..") for part in member.parts):
        raise RuntimeError(f"Unsafe path in update archive: {name}")
    target = root.joinpath(*member.parts)
    if root.resolve() not in target.resolve().parents:
        raise RuntimeError(f"Unsafe path in update archive: {name}")
    return target


def archive_update() -> bool:
    revision_file = APP_DIR / "UPSTREAM_REVISION"
    if not revision_file.is_file():
        print("ClearMark updater: no package revision found; automatic update skipped.")
        return False
    base = revision_file.read_text(encoding="ascii").strip()
    if len(base) != 40 or any(c not in "0123456789abcdef" for c in base.lower()):
        print("ClearMark updater: package revision is invalid; automatic update skipped.")
        return False

    comparison = json.loads(request(f"{API}/compare/{base}...{BRANCH}").decode("utf-8"))
    if comparison.get("status") != "ahead" or not comparison.get("ahead_by"):
        return False
    latest = comparison.get("merge_base", {}).get("sha")
    # The API's comparison head is the newest commit on the requested branch.
    latest = comparison.get("commits", [{}])[-1].get("sha") or latest
    if not latest:
        raise RuntimeError("GitHub did not return the update revision")

    archive_url = f"https://api.github.com/repos/{REPOSITORY}/tarball/{urllib.parse.quote(latest)}"
    payload = request(archive_url, "application/vnd.github+json")
    # Keep staging on the install filesystem so final replacements cannot fail
    # with EXDEV when /tmp is mounted separately.
    with tempfile.TemporaryDirectory(prefix=".clearmark-update-", dir=APP_DIR) as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            members = bundle.getmembers()
            if not members:
                raise RuntimeError("GitHub returned an empty source archive")
            prefix = PurePosixPath(members[0].name).parts[0]
            for member in members:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != prefix or len(parts) == 1:
                    continue
                relative = PurePosixPath(*parts[1:]).as_posix()
                # Runtime data, app-local optional tools, and the package's own
                # revision marker belong to the user installation.
                if relative == "UPSTREAM_REVISION" or relative.split("/", 1)[0] in {
                    ".git", ".venv", ".runtime", "tools", "clearmark.tar.gz"
                }:
                    continue
                target = safe_member_path(stage, relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise RuntimeError(f"Could not read update file: {relative}")
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o777)
                else:
                    raise RuntimeError("GitHub update contains a link or special file")

        # Files are staged and path-checked before replacing any installed files.
        for source in stage.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(stage)
            destination = APP_DIR / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        revision_file.write_text(latest + "\n", encoding="ascii")

        # Remove helpers left behind by versions that stored these scripts at
        # the project root. Keep the public launchers there for compatibility.
        for legacy_name in (
            "bootstrap-uv.ps1",
            "install_optional.py",
            "register-linux-app.py",
            "register-windows-app.ps1",
            "update.py",
        ):
            try:
                (APP_DIR / legacy_name).unlink(missing_ok=True)
            except OSError:
                # A locked legacy file is harmless; the new launcher uses scripts/.
                pass

    print("ClearMark updated from GitHub.")
    return True


def main() -> int:
    try:
        git_result = local_git_update()
        if git_result is None:
            archive_update()
    except Exception as exc:
        print(f"ClearMark updater: GitHub update check failed; continuing with this version ({exc}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
