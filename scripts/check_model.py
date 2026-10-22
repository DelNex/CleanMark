"""Initialize the LaMa model before starting the desktop application."""
from pathlib import Path
import sys

import torch

# Running a script by filename puts ``scripts`` at sys.path[0], not the app
# directory. Add the project root so the app's remover module is importable.
APP_DIRECTORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIRECTORY))


def main() -> None:
    checkpoint = Path(torch.hub.get_dir()) / "checkpoints" / "big-lama.pt"
    if not checkpoint.is_file():
        import remover

        remover._get_lama()


if __name__ == "__main__":
    main()
