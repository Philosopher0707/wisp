"""Generate Wisp app icons at all sizes."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import math
import os

SIZES = [16, 32, 128, 256, 512]
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def make_icon(size):
    """Render the Wisp logo at the given pixel size."""
    # High-res canvas for anti-aliasing, then downsample
    scale = 4
    W = size * scale
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Background circle ──
    margin = int(W * 0.08)
    bbox = (margin, margin, W - margin, W - margin)
    # Gradient-ish: deep navy to indigo
    for r in range(W // 2, margin - 1, -1):
        t = (r - margin) / (W // 2 - margin)
        r_col = int(15 + t * 20)
        g_col = int(20 + t * 30)
        b_col = int(50 + t * 60)
        draw.ellipse(
            (W // 2 - r, W // 2 - r, W // 2 + r, W // 2 + r),
            fill=(r_col, g_col, b_col, 255),
        )

    # ── Subtle rim light ──
    draw.ellipse(bbox, outline=(60, 100, 180, 120), width=max(2, W // 256))

    # ── The "W" mark ──
    cx, cy = W // 2, W // 2
    stroke = max(4, W // 64)

    # Color: electric cyan with glow
    glow_col = (80, 220, 255, 60)
    core_col = (140, 255, 255, 255)

    # Draw glow layers
    for offset in (stroke * 3, stroke * 2, stroke):
        _draw_w(draw, cx, cy, W, stroke + offset, glow_col)
    _draw_w(draw, cx, cy, W, stroke, core_col)

    # Subtle white highlight on top-left quadrant
    hl = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    hld = ImageDraw.Draw(hl)
    hld.ellipse(
        (margin + W // 8, margin + W // 8, cx, cy), fill=(255, 255, 255, 20)
    )
    img = Image.alpha_composite(img, hl)

    # Downsample with Lanczos for crisp edges
    return img.resize((size, size), Image.LANCZOS)


def _draw_w(draw, cx, cy, W, stroke, color):
    """Draw a stylised W that looks like a wisp/lightning bolt."""
    s = W * 0.42  # half-width of the W
    top_y = cy - W * 0.28
    mid_y = cy + W * 0.06
    bot_y = cy + W * 0.30

    # Four points of the W, curving organically
    p1 = (cx - s * 0.85, top_y)          # top-left
    p2 = (cx - s * 0.20, mid_y)          # inner-left valley
    p3 = (cx, top_y + W * 0.05)          # centre peak (slightly curved up)
    p4 = (cx + s * 0.20, mid_y)          # inner-right valley
    p5 = (cx + s * 0.85, top_y)          # top-right

    # Smooth curve segments
    draw.line([p1, p2], fill=color, width=stroke, joint="curve")
    draw.line([p2, p3], fill=color, width=stroke, joint="curve")
    draw.line([p3, p4], fill=color, width=stroke, joint="curve")
    draw.line([p4, p5], fill=color, width=stroke, joint="curve")

    # Bottom V tails — extend downward like roots/lightning
    b_left = (cx - s * 0.55, bot_y)
    b_right = (cx + s * 0.55, bot_y)
    draw.line([p2, b_left], fill=color, width=stroke, joint="curve")
    draw.line([p4, b_right], fill=color, width=stroke, joint="curve")

    # Small dot at the peak like a spark
    r = stroke
    draw.ellipse((p3[0] - r, p3[1] - r, p3[0] + r, p3[1] + r), fill=color)


def build_iconset():
    """Write every size for macOS .iconset + a huge 1024 preview."""
    iconset = os.path.join(OUT_DIR, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)

    # macOS iconset sizes (1x + 2x per "logical" size)
    for sz in SIZES:
        # 1x
        icon = make_icon(sz)
        icon.save(os.path.join(iconset, f"icon_{sz}x{sz}.png"))
        # 2x
        icon2x = make_icon(sz * 2)
        icon2x.save(os.path.join(iconset, f"icon_{sz}x{sz}@2x.png"))

    # Big preview / web use
    preview = make_icon(1024)
    preview.save(os.path.join(OUT_DIR, "wisp_logo_1024.png"))

    print(f"Icons written to {iconset}")
    print(f"Preview written to {os.path.join(OUT_DIR, 'wisp_logo_1024.png')}")


if __name__ == "__main__":
    build_iconset()
