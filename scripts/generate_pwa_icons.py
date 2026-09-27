"""Generate PWA icons and Open Graph cover from existing brand assets."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "static"
ICONS = ROOT / "icons"
ICONS.mkdir(exist_ok=True)

src = Image.open(ROOT / "logo.jpg").convert("RGBA")
w, h = src.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
src = src.crop((left, top, left + side, top + side))


def make(size: int, path: Path, bg=None, pad_ratio: float = 0.0) -> None:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if pad_ratio:
        inner = int(size * (1 - pad_ratio))
        icon = src.resize((inner, inner), Image.Resampling.LANCZOS)
        off = (size - inner) // 2
        canvas.paste(icon, (off, off), icon)
    else:
        icon = src.resize((size, size), Image.Resampling.LANCZOS)
        canvas.paste(icon, (0, 0), icon)

    if bg and len(bg) == 3:
        out = Image.new("RGB", (size, size), bg)
        out.paste(canvas, mask=canvas.split()[-1])
        out.save(path, "PNG", optimize=True)
    else:
        canvas.save(path, "PNG", optimize=True)
    print("wrote", path.name, size)


for s in (72, 96, 128, 144, 152, 192, 384, 512):
    make(s, ICONS / f"icon-{s}.png")

for s in (192, 512):
    make(s, ICONS / f"maskable-{s}.png", bg=(47, 134, 212), pad_ratio=0.22)

make(180, ICONS / "apple-touch-icon.png")
make(32, ICONS / "favicon-32.png")
make(16, ICONS / "favicon-16.png")

og = Image.open(ROOT / "9762667.png").convert("RGB")
target_w, target_h = 1200, 630
ow, oh = og.size
scale = max(target_w / ow, target_h / oh)
nw, nh = int(ow * scale), int(oh * scale)
og = og.resize((nw, nh), Image.Resampling.LANCZOS)
left = (nw - target_w) // 2
top = (nh - target_h) // 2
og = og.crop((left, top, left + target_w, top + target_h))
og_path = ICONS / "og-cover.jpg"
og.save(og_path, "JPEG", quality=88, optimize=True)
print("wrote", og_path.name)
print("done")
