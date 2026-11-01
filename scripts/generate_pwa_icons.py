"""Generate PWA icons and Open Graph cover in ЕЦМ brand style (#141414)."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "static"
ICONS = ROOT / "icons"
ICONS.mkdir(exist_ok=True)

INK = (20, 20, 20)  # #141414
PAPER = (246, 246, 246)  # #f6f6f6
WHITE = (255, 255, 255)


def draw_mark(size: int, *, bg=INK, fg=WHITE, pad_ratio: float = 0.18) -> Image.Image:
    """Square mark: frame + three bars (analytics)."""
    img = Image.new("RGBA", (size, size), (*bg, 255) if len(bg) == 3 else bg)
    draw = ImageDraw.Draw(img)
    pad = int(size * pad_ratio)
    # outer frame
    stroke = max(2, size // 28)
    draw.rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        outline=(*fg, 255),
        width=stroke,
    )
    # bars
    inner = size - 2 * pad
    bar_w = max(2, int(inner * 0.12))
    gap = int(inner * 0.14)
    base_y = size - pad - int(inner * 0.12)
    heights = [0.42, 0.62, 0.82]
    start_x = pad + int((inner - (3 * bar_w + 2 * gap)) / 2)
    for i, h_ratio in enumerate(heights):
        x0 = start_x + i * (bar_w + gap)
        y0 = base_y - int(inner * h_ratio)
        draw.rectangle([x0, y0, x0 + bar_w, base_y], fill=(*fg, 255))
    return img


def save_png(img: Image.Image, path: Path) -> None:
    out = img.convert("RGBA")
    out.save(path, "PNG", optimize=True)
    print("wrote", path.name, out.size[0])


for s in (72, 96, 128, 144, 152, 192, 384, 512):
    save_png(draw_mark(s), ICONS / f"icon-{s}.png")

# maskable: safe zone padding on dark square
for s in (192, 512):
    save_png(draw_mark(s, pad_ratio=0.26), ICONS / f"maskable-{s}.png")

save_png(draw_mark(180), ICONS / "apple-touch-icon.png")
save_png(draw_mark(32, pad_ratio=0.16), ICONS / "favicon-32.png")
save_png(draw_mark(16, pad_ratio=0.14), ICONS / "favicon-16.png")

# also write app.ico from 32/64/128
try:
    ico_sizes = [16, 32, 48, 64, 128]
    ico_images = [draw_mark(s).convert("RGBA") for s in ico_sizes]
    ico_path = ROOT / "app.ico"
    ico_images[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_images[1:],
    )
    print("wrote", ico_path.name)
except Exception as exc:
    print("ico skip:", exc)

# OG cover from analytics art if present, else brand panel
art = ROOT / "art-analytics.png"
src_art = art if art.exists() else ROOT / "9762667.png"
if src_art.exists():
    og = Image.open(src_art).convert("RGB")
else:
    og = Image.new("RGB", (1200, 630), INK)
target_w, target_h = 1200, 630
ow, oh = og.size
scale = max(target_w / ow, target_h / oh)
nw, nh = int(ow * scale), int(oh * scale)
og = og.resize((nw, nh), Image.Resampling.LANCZOS)
left = (nw - target_w) // 2
top = (nh - target_h) // 2
og = og.crop((left, top, left + target_w, top + target_h))
# subtle brand bar
draw = ImageDraw.Draw(og)
draw.rectangle([0, 0, target_w, 8], fill=INK)
og_path = ICONS / "og-cover.jpg"
tmp = ICONS / "og-cover-new.jpg"
og.save(tmp, "JPEG", quality=88, optimize=True)
try:
    og_path.unlink(missing_ok=True)
    tmp.replace(og_path)
except Exception:
    pass
print("wrote", og_path.name)
print("done")
