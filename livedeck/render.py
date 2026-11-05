"""Rendu des touches (Pillow) — la même image sert à l'écran et au Stream Deck."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=None)
def _font_path() -> str | None:
    return next((p for p in FONT_CANDIDATES if Path(p).exists()), None)


@lru_cache(maxsize=256)
def font(px: int) -> ImageFont.ImageFont:
    path = _font_path()
    return ImageFont.truetype(path, px) if path else ImageFont.load_default(px)


def hex_rgb(color: str, default=(0, 0, 0)) -> tuple[int, int, int]:
    c = (color or "").lstrip("#")
    if len(c) != 6:
        return default
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return default


def _wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        if not words:
            lines.append("")
            continue
        for w in words:
            cand = f"{cur} {w}".strip()
            if draw.textlength(cand, font=f) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = w
            while draw.textlength(cur, font=f) > width and len(cur) > 1:  # mot trop long : on coupe
                i = len(cur)
                while i > 1 and draw.textlength(cur[:i], font=f) > width:
                    i -= 1
                lines.append(cur[:i])
                cur = cur[i:]
        lines.append(cur)
    return lines


def _layout(draw, text, size_px, box_w, box_h):
    """Plus grande police qui fait tenir le texte, sans couper un mot tant que c'est possible."""
    if size_px:
        f = font(size_px)
        return f, _wrap(draw, text, f, box_w), int(size_px * 1.12)
    words = text.split()
    for px in range(int(box_h * 0.34), 7, -1):
        f = font(px)
        if any(draw.textlength(w, font=f) > box_w for w in words) and px > 9:  # un mot coupé se lit mal : plutôt plus petit
            continue
        lines = _wrap(draw, text, f, box_w)
        lh = int(px * 1.12)
        if lh * len(lines) <= box_h:
            return f, lines, lh
    f = font(8)
    return f, _wrap(draw, text, f, box_w), 9


@lru_cache(maxsize=2048)
def _render(size: int, bg: str, fg: str, label: str, text_px: int, icon: str,
            bar: str, pressed: bool, warn: bool, progress: float, height: int = 0) -> Image.Image:
    """Vignette carrée de `size` px, ou rectangulaire si `height` est donné (bandeaux du Loupedeck).
    `s` reste la petite dimension : elle fixe l'échelle des marges, de la jauge et du cadre."""
    w = size
    h_px = height or size
    s = min(w, h_px)
    bg_rgb = hex_rgb(bg)
    if pressed:
        bg_rgb = tuple(min(255, int(v + (255 - v) * 0.25)) for v in bg_rgb)
    img = Image.new("RGB", (w, h_px), bg_rgb)
    draw = ImageDraw.Draw(img)
    bar_h = int(s * 0.12) if bar else 0
    top, text_h = int(s * 0.06), h_px - int(s * 0.12) - bar_h

    if icon and Path(icon).exists():
        try:
            ic = Image.open(icon).convert("RGBA")
            box = int(s * (0.55 if label else 0.8))
            ic.thumbnail((box, box))
            img.paste(ic, ((w - ic.width) // 2, top if label else (h_px - bar_h - ic.height) // 2), ic)
            top += box + int(s * 0.02)
            text_h = h_px - top - bar_h - int(s * 0.04)
        except OSError:
            pass

    if label:
        f, lines, lh = _layout(draw, label, int(text_px * s / 72) if text_px else 0, int(w * 0.9), text_h)
        y = top + max(0, (text_h - lh * len(lines)) // 2)
        for line in lines:
            draw.text((w / 2, y + lh / 2), line, font=f, fill=hex_rgb(fg, (255, 255, 255)), anchor="mm")
            y += lh

    if bar:
        draw.rectangle((0, h_px - bar_h, w, h_px), fill=hex_rgb(bar))
    if pressed:
        b = max(2, s // 24)
        draw.rectangle((b // 2, b // 2, w - b // 2 - 1, h_px - b // 2 - 1), outline=(255, 255, 255), width=b)
    if progress:
        ph = max(3, s // 16)
        draw.rectangle((0, 0, w, ph), fill=(20, 24, 31))
        draw.rectangle((0, 0, int(w * progress), ph), fill=(45, 212, 191))
    if warn:
        r = max(4, s // 14)
        draw.ellipse((w - 3 * r, r, w - r, 3 * r), fill=(255, 159, 28))
    return img


def render_box(visual: dict | None, width: int, height: int) -> Image.Image:
    """Comme render(), mais pour une zone rectangulaire (bandeaux latéraux, écran de molette)."""
    if not visual:
        return Image.new("RGB", (width, height), (0, 0, 0))
    return _render(width, visual.get("bg", "#000000"), visual.get("fg", "#ffffff"),
                   visual.get("label", ""), int(visual.get("size") or 0), visual.get("icon", ""),
                   visual.get("bar") or "", bool(visual.get("pressed")), bool(visual.get("warn")),
                   float(visual.get("progress") or 0), height)


def render(visual: dict | None, size: int) -> Image.Image:
    return render_box(visual, size, size)


def render_strip(visuals, width: int = 60, height: int = 270) -> Image.Image:
    """Bandeau latéral : les zones empilées, séparées par un filet sombre."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    n = max(1, len(visuals))
    zh = height // n
    for i, v in enumerate(visuals):
        y = i * zh
        img.paste(render_box(v, width, (height - y) if i == n - 1 else zh), (0, y))
    draw = ImageDraw.Draw(img)
    for i in range(1, n):
        draw.line((0, i * zh, width, i * zh), fill=(0, 0, 0), width=2)
    return img


def render_wheel(visual: dict | None, size: int = 240) -> Image.Image:
    """Écran rond de la molette centrale : le contenu est inscrit dans le disque visible."""
    img = Image.new("RGB", (size, size), (0, 0, 0))
    if not visual:
        return img
    inner = int(size * 0.72)
    img.paste(render_box(visual, inner, inner), ((size - inner) // 2, (size - inner) // 2))
    draw = ImageDraw.Draw(img)
    ring = hex_rgb(visual.get("lit") or visual.get("bg") or "#1f2937")
    draw.ellipse((2, 2, size - 3, size - 3), outline=ring, width=max(3, size // 40))
    return img
