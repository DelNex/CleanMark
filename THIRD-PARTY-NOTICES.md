# Third-party notices

ClearMark's own source code is MIT licensed (see [LICENSE](LICENSE)). It builds on the
components below, each under its own terms. Nothing here is legal advice — if you plan
to use ClearMark commercially, read the originals and take your own advice.

---

## AI model — the one with a real restriction

**LaMa (big-lama)** — resolution-robust large-mask inpainting, by Samsung AI Center.

- Project: <https://github.com/advimman/lama>
- Weights obtained from: <https://github.com/enesmsahin/simple-lama-inpainting>
- LaMa **source code**: Apache License 2.0
- LaMa **pretrained weights** (`big-lama.pt`): **CC BY-NC-SA 4.0 — non-commercial use only**

**What this means in practice:**

- ✅ **Publishing this repository is fine.** ClearMark does not contain or redistribute
  the weights — `remover.py` downloads them to your own machine on first run. What is
  published here is a URL and the code that calls it.
- ✅ **Personal, research and educational use is fine.**
- ❌ **Commercial use is not.** You cannot sell ClearMark, run it as a paid service, or
  use its output commercially while it uses these weights. `NC` means non-commercial,
  and `SA` (share-alike) means derivatives of the *model* must carry the same terms.
- 🔁 To lift the restriction, swap in a permissively licensed inpainting model. The
  OpenCV Telea fallback already in `remover.py` is BSD-licensed and unrestricted, though
  visibly lower quality.

---

## Bundled in this repository

**Sofia Sans** — by Lettersoup, licensed **SIL Open Font License 1.1**.
The `.woff2` files in `static/fonts/` are redistributed here, so the OFL requires the
license text to travel with them: [`static/fonts/OFL.txt`](static/fonts/OFL.txt).
OFL permits bundling and redistribution, including commercially, provided the font is
not sold on its own and the license is included.

---

## Installed by pip at setup (not redistributed here)

| Package | License |
|---|---|
| PyTorch | BSD 3-Clause |
| Flask | BSD 3-Clause |
| NumPy | BSD 3-Clause |
| OpenCV (`opencv-python-headless`) | Apache 2.0 |
| Pillow | MIT-CMU / HPND |
| `imageio-ffmpeg` | BSD 2-Clause (wrapper) |

`imageio-ffmpeg` ships a prebuilt **FFmpeg** binary, which is licensed **LGPL 2.1** (some
builds GPL). ClearMark calls it as a separate executable and does not link against it,
which is the usual arrangement for LGPL compliance. If you ever bundle ClearMark into a
single distributable binary, review this before shipping.

**ExifTool** (optional, downloaded locally when selected) — Perl Artistic License / GPL.
It is fetched from the official ExifTool site and stored under `tools/exiftool/`; it is
not bundled in the ClearMark archive.

---

## Intended use

ClearMark is a general-purpose image and video inpainting tool. Inpainting — rebuilding
a selected region of an image — is standard, widely available technology, the same thing
Photoshop's Content-Aware Fill does.

Removing a watermark does not remove the underlying copyright. You are responsible for
having the right to edit any file you process. Use it on your own images, on content you
are licensed to modify, or for research. Do not use it to strip attribution from work
that is not yours, to defeat provenance signals for deceptive purposes, or to
misrepresent AI-generated content as authentic.

The bundled **AI Content Check** tool exists to *support* provenance, not undermine it —
it reads C2PA Content Credentials and related metadata so you can tell what a file
declares about itself.
