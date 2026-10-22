"""Install a CUDA-enabled PyTorch build when an NVIDIA driver is available."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_REQUIREMENT = "torch>=2.2"


def nvidia_smi() -> str | None:
    executable = shutil.which("nvidia-smi")
    if executable:
        return executable
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
        if program_files:
            candidate = Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def detect_nvidia() -> tuple[str | None, list[str]]:
    executable = nvidia_smi()
    if not executable:
        return None, []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return executable, []
    if result.returncode:
        return executable, []
    return executable, [line.strip() for line in result.stdout.splitlines() if line.strip()]


def probe_torch() -> dict[str, object] | None:
    code = (
        "import json, torch; "
        "available = torch.cuda.is_available(); "
        "print(json.dumps({'available': available, 'cuda': torch.version.cuda, "
        "'device': torch.cuda.get_device_name(0) if available else None}))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Could not check PyTorch: {exc}")
        return None
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            print(f"PyTorch check failed: {detail}")
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        print("PyTorch returned an unrecognized device status.")
        return None


def install_torch(uv: str | None, *, cuda: bool) -> int:
    requirement = TORCH_REQUIREMENT
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable]
    else:
        command = [sys.executable, "-m", "pip", "install"]
    command.append("--upgrade")
    if cuda:
        command.extend(["--index-url", CUDA_INDEX])
    command.append(requirement)
    print("Installing CUDA-enabled PyTorch..." if cuda else "Installing PyTorch...")
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", help="uv executable used to manage this environment")
    args = parser.parse_args()

    nvidia_command, gpu_names = detect_nvidia()
    torch_status = probe_torch()

    if not gpu_names:
        if nvidia_command:
            print("NVIDIA-SMI is present but did not report a usable NVIDIA GPU.")
        else:
            print("No NVIDIA GPU/driver detected; ClearMark will use CPU or Apple GPU mode.")
        if torch_status is None:
            if install_torch(args.uv, cuda=False):
                print("Could not install PyTorch.", file=sys.stderr)
                return 1
        return 0

    print("NVIDIA GPU detected: " + ", ".join(gpu_names))
    if torch_status and torch_status.get("available"):
        print(f"PyTorch CUDA is ready: {torch_status.get('device')} (CUDA {torch_status.get('cuda')}).")
        return 0

    # A CPU-only PyTorch wheel cannot use an NVIDIA card. Replace it with the
    # official CUDA 12.6 wheel; the CUDA runtime ships with the wheel, while the
    # machine still needs a working NVIDIA display driver.
    if not torch_status or not torch_status.get("cuda"):
        if install_torch(args.uv, cuda=True):
            print("CUDA PyTorch installation failed; ClearMark will continue in CPU mode.", file=sys.stderr)
            return 0
        torch_status = probe_torch()

    if torch_status and torch_status.get("available"):
        print(f"PyTorch CUDA is ready: {torch_status.get('device')} (CUDA {torch_status.get('cuda')}).")
    else:
        cuda_version = torch_status.get("cuda") if torch_status else None
        print(
            "An NVIDIA GPU was detected, but PyTorch cannot access it "
            f"(CUDA build: {cuda_version or 'CPU-only'}). Update the NVIDIA driver "
            "and run ClearMark again; it will use the CPU until CUDA is available.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
