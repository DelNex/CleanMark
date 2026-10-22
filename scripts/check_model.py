"""Initialize the LaMa model before starting the desktop application."""
from pathlib import Path

import torch


def main() -> None:
    checkpoint = Path(torch.hub.get_dir()) / "checkpoints" / "big-lama.pt"
    if not checkpoint.is_file():
        import remover

        remover._get_lama()


if __name__ == "__main__":
    main()
