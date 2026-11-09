"""
AI Video Flow - motion-graphics ads from photos + a voiceover

The same technique the hand-made ads use (kinetic gold headlines, cards, chips, a mentor cut-out, subtitles,
every cue landing on a spoken word), driven by data instead of code:

    voiceover --(server speech-to-text)--> words with timestamps
    words + photos + the editor's prompt --(Claude)--> a scene spec (JSON)
    scene spec --(this renderer: Pillow + FFmpeg)--> MP4

Editing is the same loop: the current spec + "make the headline bigger" --> a new spec --> v2.mp4.
Positions are fractions of the frame and sizes are in pixels of a frame whose short side is 1080,
so one spec renders at 9:16, 1:1 or 16:9.
"""

import os
import re
import json
import math
import time
import hashlib
import logging
import subprocess
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger("ai_video_flow.motion_ad")

FPS = 30
TRANS = 0.18  # outgoing scene fade
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
STILL_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".psd", ".tif", ".tiff", ".bmp")
ELEMENT_TYPES = ("label", "headline", "gold", "chip", "card", "icon_card", "image", "grid", "timeline",
                 "logos", "clock", "button", "progress", "lower_third")
ICONS = ("none", "camera", "film", "clock", "check", "hourglass", "target", "spark", "dot", "ticket", "play",
         "star", "users", "calendar", "rupee", "mic", "bolt", "heart")
THEMES = {
    # background centre / edge, accent gradient top / bottom, label colour, card top / bottom / outline, glow
    "gold_dark": dict(bg0=(46, 32, 14), bg1=(5, 4, 3), gold_t=(255, 226, 140), gold_b=(222, 156, 30), label=(214, 196, 160),
                      card_t=(46, 36, 22), card_b=(22, 17, 11), outline=(232, 185, 95), glow=(255, 190, 60), stars=True),
    "navy_gold": dict(bg0=(30, 46, 128), bg1=(5, 8, 30), gold_t=(255, 228, 138), gold_b=(226, 160, 28), label=(168, 180, 218),
                      card_t=(60, 78, 170), card_b=(28, 40, 108), outline=(120, 140, 230), glow=(255, 190, 60), stars=False),
    "purple_neon": dict(bg0=(58, 22, 92), bg1=(8, 4, 18), gold_t=(255, 214, 255), gold_b=(196, 98, 255), label=(200, 180, 230),
                        card_t=(70, 36, 110), card_b=(30, 14, 52), outline=(196, 120, 255), glow=(196, 98, 255), stars=True),
}
WHITE = (242, 244, 250)
NAVY_TXT = (24, 18, 8)
GREEN = (52, 211, 153)
SIZES = {"headline": {"s": 54, "m": 78, "l": 108, "xl": 150}, "gold": {"s": 70, "m": 104, "l": 150, "xl": 210},
         "label": {"s": 24, "m": 30, "l": 38, "xl": 46}, "chip": {"s": 32, "m": 42, "l": 54, "xl": 64}}


class MotionAdError(RuntimeError):
    pass


# ---------------------------------------------------------------- fonts (macOS / Windows / fallback)
_FONT_FILES = {
    "heavy": [("/System/Library/Fonts/Avenir Next.ttc", 8), ("/System/Library/Fonts/Supplemental/Arial Black.ttf", 0),
              ("C:/Windows/Fonts/seguibl.ttf", 0), ("C:/Windows/Fonts/ariblk.ttf", 0), ("C:/Windows/Fonts/arialbd.ttf", 0)],
    "bold": [("/System/Library/Fonts/Avenir Next.ttc", 0), ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
             ("C:/Windows/Fonts/segoeuib.ttf", 0), ("C:/Windows/Fonts/arialbd.ttf", 0)],
    "mono": [("/System/Library/Fonts/Menlo.ttc", 1), ("/System/Library/Fonts/Supplemental/Courier New Bold.ttf", 0),
             ("C:/Windows/Fonts/consolab.ttf", 0), ("C:/Windows/Fonts/courbd.ttf", 0)],
}


@lru_cache(None)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path, index in _FONT_FILES[kind]:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, max(8, int(size)), index=index)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


# Fonts that carry ₹ and / or Hindi; the first one that has every missing character is used
_FALLBACK_FILES = [("/System/Library/Fonts/Helvetica.ttc", 1), ("/System/Library/Fonts/Kohinoor.ttc", 4),
                   ("C:/Windows/Fonts/segoeuib.ttf", 0), ("C:/Windows/Fonts/NirmalaB.ttf", 0), ("C:/Windows/Fonts/Nirmala.ttf", 0)]


@lru_cache(None)
def _missing(f, ch: str) -> bool:
    """True when the font draws its 'missing glyph' box for ch."""
    try:
        return bytes(f.getmask(ch)) == bytes(f.getmask("\uffff"))
    except Exception:
        return False


@lru_cache(None)
def _fallback(size: int, chars: str):
    for path, index in _FALLBACK_FILES:
        if os.path.isfile(path):
            try:
                fb = ImageFont.truetype(path, size, index=index)
            except Exception:
                continue
            if not any(_missing(fb, c) for c in chars):
                return fb
    return None


def font_for(f, txt: str):
    """f, or a fallback font of the same size when f cannot draw some character of txt (₹, Hindi…)."""
    missing = "".join(sorted(c for c in set(txt) if not c.isascii() and _missing(f, c)))
    if not missing:
        return f
    return _fallback(f.size, missing) or f


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(p):
    return 1 - (1 - p) ** 3


def ease_back(p, s=1.9):
    p -= 1
    return 1 + p * p * ((s + 1) * p + s)


# ---------------------------------------------------------------- drawing primitives
def text_mask(txt: str, f, spacing: int = 0, pad: int = 4) -> Image.Image:
    f = font_for(f, txt)
    if spacing:
        w = sum((f.getbbox(c)[2] - f.getbbox(c)[0]) if c != " " else f.size // 3 for c in txt) + spacing * max(0, len(txt) - 1)
    else:
        b = f.getbbox(txt or " ")
        w = b[2] - b[0]
    asc, desc = f.getmetrics()
    m = Image.new("L", (max(1, w) + pad * 2, asc + desc + pad * 2), 0)
    d = ImageDraw.Draw(m)
    if spacing:
        x = pad
        for c in txt:
            if c == " ":
                x += f.size // 3 + spacing
                continue
            bb = f.getbbox(c)
            d.text((x - bb[0], pad), c, font=f, fill=255)
            x += bb[2] - bb[0] + spacing
    else:
        d.text((pad - f.getbbox(txt or " ")[0], pad), txt, font=f, fill=255)
    return m.crop(m.getbbox()) if m.getbbox() else m


def vgrad(size, top, bot) -> Image.Image:
    w, h = max(1, size[0]), max(1, size[1])
    t = np.linspace(0, 1, h)[:, None, None]
    arr = np.array(top)[None, None] * (1 - t) + np.array(bot)[None, None] * t
    return Image.fromarray(np.repeat(arr, w, 1).astype(np.uint8), "RGB")


def pad_canvas(m: Image.Image, pad: int) -> Image.Image:
    c = Image.new(m.mode, (m.width + pad * 2, m.height + pad * 2), 0)
    c.paste(m, (pad, pad))
    return c


def colored(mask: Image.Image, color) -> Image.Image:
    lay = Image.new("RGBA", mask.size, tuple(color) + (0,))
    lay.putalpha(mask)
    return lay


def glow_layer(mask: Image.Image, color, radius: float, strength: float) -> Image.Image:
    g = mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))
    return colored(g, color)


def rrect(size, r, fill, outline=None, width=2) -> Image.Image:
    im = Image.new("RGBA", (max(1, int(size[0])), max(1, int(size[1]))), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, im.width - 1, im.height - 1), int(r), fill=fill, outline=outline, width=width)
    return im


def icon(name: str, s: int, color) -> Image.Image:
    s = max(8, int(s))
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(3, s // 14)
    c = tuple(color)
    if name == "camera":
        d.rounded_rectangle((s * .1, s * .3, s * .9, s * .82), s * .1, outline=c, width=lw)
        d.rectangle((s * .34, s * .2, s * .56, s * .3), fill=c)
        d.ellipse((s * .34, s * .4, s * .66, s * .72), outline=c, width=lw)
    elif name == "film":
        d.rounded_rectangle((s * .12, s * .22, s * .88, s * .82), s * .06, outline=c, width=lw)
        for i in range(4):
            x = s * .2 + i * s * .18
            d.rectangle((x, s * .27, x + s * .08, s * .33), fill=c)
            d.rectangle((x, s * .71, x + s * .08, s * .77), fill=c)
        d.polygon([(s * .42, s * .42), (s * .42, s * .62), (s * .6, s * .52)], fill=c)
    elif name == "clock":
        d.ellipse((lw, lw, s - lw, s - lw), outline=c, width=lw)
        d.line([(s / 2, s / 2), (s / 2, s * .22)], fill=c, width=lw)
        d.line([(s / 2, s / 2), (s * .7, s * .6)], fill=c, width=lw)
    elif name == "check":
        d.ellipse((lw, lw, s - lw, s - lw), outline=c, width=lw)
        d.line([(s * .3, s * .52), (s * .45, s * .66), (s * .72, s * .36)], fill=c, width=lw, joint="curve")
    elif name == "hourglass":
        for y in (.15, .85):
            d.line([(s * .25, s * y), (s * .75, s * y)], fill=c, width=lw)
        d.line([(s * .3, s * .15), (s * .7, s * .85)], fill=c, width=lw)
        d.line([(s * .7, s * .15), (s * .3, s * .85)], fill=c, width=lw)
    elif name == "target":
        for k in (0.05, 0.25):
            d.ellipse((s * k, s * k, s * (1 - k), s * (1 - k)), outline=c, width=lw)
        d.ellipse((s * .42, s * .42, s * .58, s * .58), fill=c)
    elif name in ("spark", "star"):
        cx = cy = s / 2
        n = 8 if name == "spark" else 10
        pts = [(cx + (s * .46 if i % 2 == 0 else s * (.13 if name == "spark" else .2)) * math.cos(math.pi * 2 / n * i - math.pi / 2),
                cy + (s * .46 if i % 2 == 0 else s * (.13 if name == "spark" else .2)) * math.sin(math.pi * 2 / n * i - math.pi / 2))
               for i in range(n)]
        d.polygon(pts, fill=c)
    elif name == "dot":
        d.ellipse((s * .25, s * .25, s * .75, s * .75), fill=c)
    elif name == "ticket":
        d.rounded_rectangle((s * .08, s * .25, s * .92, s * .75), s * .08, outline=c, width=lw)
        d.line([(s * .5, s * .32), (s * .5, s * .68)], fill=c, width=lw)
    elif name == "play":
        d.ellipse((lw, lw, s - lw, s - lw), outline=c, width=lw)
        d.polygon([(s * .42, s * .32), (s * .42, s * .68), (s * .7, s * .5)], fill=c)
    elif name == "users":
        d.ellipse((s * .3, s * .12, s * .7, s * .5), outline=c, width=lw)
        d.arc((s * .12, s * .55, s * .88, s * 1.25), 180, 360, fill=c, width=lw)
    elif name == "calendar":
        d.rounded_rectangle((s * .12, s * .2, s * .88, s * .88), s * .08, outline=c, width=lw)
        d.line([(s * .12, s * .38), (s * .88, s * .38)], fill=c, width=lw)
        for x in (.32, .68):
            d.line([(s * x, s * .1), (s * x, s * .28)], fill=c, width=lw)
    elif name == "rupee":
        f = font_for(font("heavy", int(s * .8)), "₹")
        d.text((s / 2, s / 2), "₹", font=f, fill=c, anchor="mm")
    elif name == "mic":
        d.rounded_rectangle((s * .36, s * .1, s * .64, s * .6), s * .14, outline=c, width=lw)
        d.arc((s * .22, s * .3, s * .78, s * .74), 0, 180, fill=c, width=lw)
        d.line([(s * .5, s * .74), (s * .5, s * .9)], fill=c, width=lw)
    elif name == "bolt":
        d.polygon([(s * .58, s * .06), (s * .22, s * .56), (s * .48, s * .56), (s * .4, s * .94), (s * .78, s * .42), (s * .52, s * .42)], fill=c)
    elif name == "heart":
        d.ellipse((s * .12, s * .2, s * .52, s * .58), fill=c)
        d.ellipse((s * .48, s * .2, s * .88, s * .58), fill=c)
        d.polygon([(s * .14, s * .46), (s * .86, s * .46), (s * .5, s * .88)], fill=c)
    elif name == "cursor":
        d.polygon([(0, 0), (0, s * .8), (s * .22, s * .62), (s * .38, s * .98), (s * .52, s * .92), (s * .36, s * .56), (s * .62, s * .56)],
                  fill=(255, 255, 255), outline=(0, 0, 0))
    return im


# ---------------------------------------------------------------- the renderer
class Renderer:
    def __init__(self, spec: Dict[str, Any], assets: List[str], audio: str, aspect: str, words: List[Dict[str, Any]],
                 logo: str = ""):
        import copy
        spec = copy.deepcopy(spec)  # layout moves elements; the saved spec keeps Claude's values for the next edit
        self.spec, self.assets, self.audio, self.words, self.logo = spec, assets, audio, words, logo
        self.W, self.H = ASPECTS.get(aspect, (1080, 1920))
        self.k = min(self.W, self.H) / 1080.0
        self.th = THEMES.get(spec.get("theme"), THEMES["gold_dark"])
        self.duration = float(spec.get("duration") or 10.0)
        self.scenes = spec.get("scenes") or []
        self.phrases = self._phrases(words) if spec.get("subtitles", True) else []
        self._cache: Dict[Any, Image.Image] = {}
        for sc in self.scenes:
            self.layout(sc)

    def element_size(self, el: Dict[str, Any]) -> Tuple[int, int]:
        """Drawn size of an element in pixels (animated widgets: their usual footprint)."""
        nominal = {"grid": (7 * 128, 4 * 128), "timeline": (int(self.W * 0.82 / self.k), 400), "logos": (800, 230),
                   "clock": (320, 320), "button": (520, 170), "progress": (int(self.W * 0.72 / self.k), 90)}
        if el["type"] in nominal:
            w, h = nominal[el["type"]]
            return self.px(w), self.px(h)
        im = self.element_image(el)
        return (im.width, im.height) if im is not None else (0, 0)

    def layout(self, sc: Dict[str, Any]):
        """Pushes elements down (and, if the stack runs out of room, the whole stack up) so none overlap."""
        els = [e for e in sc.get("elements", [])]
        if len(els) < 2:
            return
        sizes = {id(e): self.element_size(e) for e in els}
        widgets = ("grid", "timeline", "logos", "clock", "button", "progress")
        bottom_limit = self.H * (0.86 if self.phrases else 0.95)
        top_limit = self.H * 0.075
        els.sort(key=lambda e: e["y"])
        placed: List[Dict[str, Any]] = []
        for e in els:
            w, h = sizes[id(e)]
            cy = e["y"] * self.H
            for o in placed:
                ow, oh = sizes[id(o)]
                if abs(e["x"] - o["x"]) * self.W < (w + ow) / 2 - self.px(40):  # same column
                    # text images carry glow padding (they may touch); widgets get real breathing room
                    gap = self.px(28) if (e["type"] in widgets or o["type"] in widgets) else -self.px(16)
                    cy = max(cy, o["y"] * self.H + oh / 2 + h / 2 + gap)
            e["y"] = cy / self.H
            placed.append(e)
        low = max(e["y"] * self.H + sizes[id(e)][1] / 2 for e in els)
        if low > bottom_limit:
            top = min(e["y"] * self.H - sizes[id(e)][1] / 2 for e in els)
            shift = min(low - bottom_limit, max(0.0, top - top_limit))
            for e in els:
                e["y"] -= shift / self.H

    # ---- helpers
    def px(self, v: float) -> int:
        return max(1, int(round(v * self.k)))

    def cached(self, key, make: Callable[[], Image.Image]) -> Image.Image:
        im = self._cache.get(key)
        if im is None:
            im = self._cache[key] = make()
        return im

    def paste(self, canvas: Image.Image, im: Image.Image, cx: float, cy: float, alpha: float = 1.0, scale: float = 1.0):
        if alpha <= 0.01:
            return
        if abs(scale - 1) > 0.004:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
        if alpha < 0.999:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
        x, y = int(cx - im.width / 2), int(cy - im.height / 2)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.W, x + im.width), min(self.H, y + im.height)
        if x1 > x0 and y1 > y0:
            canvas.alpha_composite(im.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))

    # ---- text widgets
    def gold_text(self, txt: str, size: int, max_w: int = 0) -> Image.Image:
        th, max_w = self.th, max_w or int(self.W * 0.9)
        size = self.px(size)
        f = font("heavy", size)
        while f.getbbox(txt)[2] > max_w and size > 30:
            size -= 4
            f = font("heavy", size)
        m = pad_canvas(text_mask(txt, f), self.px(40))
        out = Image.new("RGBA", m.size, (0, 0, 0, 0))
        sh = m.filter(ImageFilter.GaussianBlur(self.px(10))).point(lambda v: int(v * 0.8))
        out.alpha_composite(colored(ImageChops.offset(sh, 0, self.px(8)), (0, 0, 6)))
        out.alpha_composite(glow_layer(m, th["glow"], self.px(22), 0.45))
        bb = m.getbbox() or (0, 0, m.width, m.height)
        g = Image.new("RGB", m.size, th["gold_b"])
        g.paste(vgrad((m.width, bb[3] - bb[1]), th["gold_t"], th["gold_b"]), (0, bb[1]))
        fill = g.convert("RGBA")
        fill.putalpha(m)
        out.alpha_composite(fill)
        return out

    def headline(self, txt: str, highlight: str, size: int, max_w: int = 0) -> Image.Image:
        """White heavy text, wrapped to two lines when needed; the highlight words are gold."""
        th = self.th
        size, max_w = self.px(size), max_w or int(self.W * 0.88)
        hl = {w.strip(".,!?").upper() for w in highlight.split()} if highlight else set()
        words = txt.split()
        while True:
            f = font("heavy", size)
            space = f.getbbox("A A")[2] - f.getbbox("AA")[2]
            wlen = lambda w, f=f: (lambda b: b[2] - b[0])(font_for(f, w).getbbox(w))  # width in the font that draws it
            lines, cur, cur_w = [], [], 0
            for w in words:
                ww = wlen(w)
                if cur and cur_w + space + ww > max_w:
                    lines.append(cur)
                    cur, cur_w = [], 0
                cur.append(w)
                cur_w += (space if len(cur) > 1 else 0) + ww
            if cur:
                lines.append(cur)
            if len(lines) <= 2 or size <= 30:
                break
            size -= 4
        asc, desc = f.getmetrics()
        lh = int((asc + desc) * 1.05)
        widths = [sum(wlen(w) for w in ln) + space * (len(ln) - 1) for ln in lines]
        pad = self.px(30)
        W_, H_ = max(widths) + pad * 2, lh * len(lines) + pad * 2
        white = Image.new("L", (W_, H_), 0)
        gold = Image.new("L", (W_, H_), 0)
        for i, ln in enumerate(lines):
            x = pad + (max(widths) - widths[i]) // 2
            for w in ln:
                target = gold if w.strip(".,!?").upper() in hl else white
                fw = font_for(f, w)
                ImageDraw.Draw(target).text((x - fw.getbbox(w)[0], pad + i * lh), w, font=fw, fill=255)
                x += wlen(w) + space
        both = ImageChops.lighter(white, gold)
        out = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
        out.alpha_composite(colored(ImageChops.offset(both.filter(ImageFilter.GaussianBlur(self.px(6))), 0, self.px(4)), (0, 0, 6)))
        if gold.getbbox():
            out.alpha_composite(glow_layer(gold, th["glow"], self.px(16), 0.5))
            g = vgrad((W_, H_), th["gold_t"], th["gold_b"]).convert("RGBA")
            g.putalpha(gold)
            out.alpha_composite(g)
        out.alpha_composite(colored(white, WHITE))
        return out

    def label(self, txt: str, size: int) -> Image.Image:
        m = text_mask(txt.upper(), font("mono", self.px(size)), spacing=self.px(size * 0.3))
        return colored(pad_canvas(m, self.px(10)), self.th["label"])

    def chip(self, txt: str, ic: str, gold: bool, size: int) -> Image.Image:
        th = self.th
        size = self.px(size)
        f = font("heavy", size)
        tm = text_mask(txt, f, pad=0)
        isz = size if ic != "none" else 0
        w, h = tm.width + isz + self.px(70) + (self.px(20) if isz else 0), int(size * 2.05)
        pad = self.px(30)
        out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        base = rrect((w, h), h // 2, (255, 255, 255, 255)).getchannel("A")
        if gold:
            out.alpha_composite(glow_layer(pad_canvas(base, pad), th["glow"], self.px(18), 0.55))
            g = vgrad((w, h), th["gold_t"], th["gold_b"]).convert("RGBA")
            g.putalpha(base)
            out.alpha_composite(g, (pad, pad))
            col = NAVY_TXT
        else:
            out.alpha_composite(rrect((w, h), h // 2, th["card_b"] + (235,), th["outline"] + (255,), max(2, self.px(3))), (pad, pad))
            col = th["gold_t"]
        x = pad + self.px(35)
        if isz:
            out.alpha_composite(icon(ic, isz, col), (x, pad + (h - isz) // 2))
            x += isz + self.px(20)
        out.alpha_composite(colored(tm, col), (x, pad + (h - tm.height) // 2))
        return out

    def card(self, num: str, txt: str, ic: str) -> Image.Image:
        th = self.th
        w, h, r = int(self.W * 0.82), self.px(118), self.px(26)
        pad = self.px(30)
        out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        sh = pad_canvas(rrect((w, h), r, (0, 0, 0, 255)).getchannel("A"), pad).filter(ImageFilter.GaussianBlur(self.px(16)))
        out.alpha_composite(colored(ImageChops.offset(sh, 0, self.px(10)), (0, 0, 4)))
        g = vgrad((w, h), th["card_t"], th["card_b"]).convert("RGBA")
        g.putalpha(rrect((w, h), r, (0, 0, 0, 235)).getchannel("A"))
        out.alpha_composite(g, (pad, pad))
        out.alpha_composite(rrect((w, h), r, None, th["outline"] + (140,), max(2, self.px(2))), (pad, pad))
        x = pad + self.px(30)
        if num:
            nb = self.px(70)
            out.alpha_composite(rrect((nb, nb), self.px(16), th["gold_b"] + (45,), th["gold_b"] + (210,), 2), (x, pad + (h - nb) // 2))
            nm = text_mask(num, font("heavy", self.px(34)), pad=0)
            out.alpha_composite(colored(nm, th["gold_t"]), (x + (nb - nm.width) // 2, pad + (h - nm.height) // 2))
            x += nb + self.px(26)
        else:
            ci = icon("check", self.px(62), GREEN)
            out.alpha_composite(ci, (x, pad + (h - ci.height) // 2))
            x += ci.width + self.px(26)
        f = font("heavy", self.px(44))
        right = self.px(90) if ic != "none" else self.px(30)
        while f.getbbox(txt)[2] > w - (x - pad) - right and f.size > 18:
            f = font("heavy", f.size - 2)
        tm = text_mask(txt, f, pad=0)
        out.alpha_composite(colored(tm, WHITE), (x, pad + (h - tm.height) // 2))
        if ic != "none":
            ii = icon(ic, self.px(46), th["gold_t"])
            out.alpha_composite(ii, (pad + w - self.px(76), pad + (h - ii.height) // 2))
        return out

    def icon_card(self, ic: str, line1: str, line2: str) -> Image.Image:
        th = self.th
        w, h, r = self.px(380), self.px(330), self.px(34)
        pad = self.px(40)
        out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
        base = rrect((w, h), r, (0, 0, 0, 255)).getchannel("A")
        out.alpha_composite(glow_layer(pad_canvas(base, pad), th["outline"], self.px(26), 0.35))
        g = vgrad((w, h), th["card_t"], th["card_b"]).convert("RGBA")
        g.putalpha(base)
        out.alpha_composite(g, (pad, pad))
        out.alpha_composite(rrect((w, h), r, None, th["outline"] + (170,), max(2, self.px(3))), (pad, pad))
        ii = icon(ic if ic != "none" else "spark", self.px(120), th["gold_t"])
        out.alpha_composite(ii, (pad + (w - ii.width) // 2, pad + self.px(48)))
        for i, ln in enumerate((line1, line2)):
            if not ln:
                continue
            f = font("heavy", self.px(40))
            while f.getbbox(ln)[2] > w - self.px(30) and f.size > 16:
                f = font("heavy", f.size - 2)
            tm = text_mask(ln, f, pad=0)
            out.alpha_composite(colored(tm, WHITE), (pad + (w - tm.width) // 2, pad + self.px(200) + i * self.px(54)))
        return out

    def photo(self, idx: int, size: str) -> Optional[Image.Image]:
        """A photo as a rounded card with a gold border (a PNG with transparency stays a cut-out)."""
        im = self.asset(idx)
        if im is None:
            return None
        frac = {"s": 0.32, "m": 0.46, "l": 0.62, "xl": 0.8}.get(size, 0.46)
        box = int(min(self.W, self.H) * frac)
        sc = box / max(im.width, im.height)
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
        if self.has_alpha(im):
            return im
        r, pad = self.px(26), self.px(24)
        out = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
        mask = rrect(im.size, r, (0, 0, 0, 255)).getchannel("A")
        out.alpha_composite(glow_layer(pad_canvas(mask, pad), self.th["glow"], self.px(18), 0.4))
        im.putalpha(mask)
        out.alpha_composite(im, (pad, pad))
        out.alpha_composite(rrect(im.size, r, None, self.th["outline"] + (230,), max(2, self.px(3))), (pad, pad))
        return out

    # ---- assets
    @staticmethod
    def has_alpha(im: Image.Image) -> bool:
        a = im.getchannel("A")
        return a.getextrema()[0] < 250

    def asset(self, idx: int) -> Optional[Image.Image]:
        if not (0 <= idx < len(self.assets)):
            return None

        def load():
            try:
                with Image.open(self.assets[idx]) as im:
                    im.load()
                    im = im.convert("RGBA")
            except Exception as e:
                logger.warning(f"Could not read {self.assets[idx]}: {e}")
                return None
            if self.has_alpha(im) and im.getchannel("A").getbbox():
                im = im.crop(im.getchannel("A").getbbox())
            if max(im.size) > 2400:
                sc = 2400 / max(im.size)
                im = im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS)
            return im
        return self.cached(("asset", idx), load)

    def mentor(self, idx: int) -> Optional[Image.Image]:
        """The scene's main photo: a cut-out with a soft halo and a gold ring behind the head, or a framed photo."""
        def make():
            im = self.asset(idx)
            if im is None:
                return None
            portrait = self.H >= self.W
            max_h = self.H * (0.6 if self.H > self.W * 1.3 else 0.68)
            max_w = self.W * (0.95 if portrait else 0.48)
            sc = min(max_w / im.width, max_h / im.height)
            im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
            if not self.has_alpha(im):
                return self.photo_frame(im)
            pad = self.px(120)
            out = Image.new("RGBA", (im.width + pad * 2, im.height + pad), (0, 0, 0, 0))
            a = pad_canvas(im.getchannel("A"), pad).crop((0, 0, out.width, out.height))
            r = int(im.width * 0.36)
            cx, cy = out.width // 2, pad + int(im.height * 0.33)
            halo = Image.new("L", out.size, 0)
            ImageDraw.Draw(halo).ellipse((cx - r, cy - r, cx + r, cy + r), fill=80)
            out.alpha_composite(colored(halo.filter(ImageFilter.GaussianBlur(self.px(90))), self.th["glow"]))
            ring = Image.new("RGBA", out.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((cx - r, cy - r, cx + r, cy + r), outline=self.th["outline"] + (110,), width=max(2, self.px(3)))
            out.alpha_composite(ring)
            out.alpha_composite(glow_layer(a, self.th["glow"], self.px(26), 0.5))
            out.alpha_composite(im, (pad, pad))
            return out
        return self.cached(("mentor", idx), make)

    def photo_frame(self, im: Image.Image) -> Image.Image:
        im = im.copy()
        r, pad = self.px(30), self.px(30)
        out = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
        mask = rrect(im.size, r, (0, 0, 0, 255)).getchannel("A")
        out.alpha_composite(glow_layer(pad_canvas(mask, pad), self.th["glow"], self.px(20), 0.45))
        im.putalpha(mask)
        out.alpha_composite(im, (pad, pad))
        out.alpha_composite(rrect(im.size, r, None, self.th["outline"] + (230,), max(2, self.px(3))), (pad, pad))
        return out

    # ---- animated widgets
    def draw_grid(self, canvas, lt, cx, cy, a):
        cols, rows, step = 7, 4, self.px(128)
        picks = {(1, 0), (4, 1), (2, 2), (6, 2), (0, 3), (5, 3), (3, 1)}
        tile = self.px(104)
        for r in range(rows):
            for c in range(cols):
                p = clamp((lt - (r * cols + c) * 0.018) / 0.3)
                if p <= 0:
                    continue
                on = (c, r) in picks and lt > 0.95 + 0.07 * c
                key = ("tile", on)
                im = self.cached(key, lambda on=on: (rrect((tile, tile), self.px(16), self.th["gold_b"] + (255,), self.th["gold_t"] + (255,), self.px(4))
                                                     if on else rrect((tile, tile), self.px(16), self.th["card_t"] + (255,), self.th["outline"] + (140,), 2)))
                x = cx + (c - (cols - 1) / 2) * step
                y = cy + (r - (rows - 1) / 2) * step
                self.paste(canvas, im, x, y + (1 - ease_out(p)) * self.px(30), a * ease_out(p),
                           1.0 + (0.08 * math.sin(clamp((lt - 0.95 - 0.07 * c) / 0.25) * math.pi) if on else 0))

    def draw_timeline(self, canvas, lt, cx, cy, a, label_txt: str):
        """An editing timeline: tracks of clips fill in, the picked clips glow gold, a progress bar runs under it."""
        th = self.th
        w, tracks = int(self.W * 0.82), 6
        th_h, gap = self.px(34), self.px(14)
        h = tracks * (th_h + gap) + self.px(110)
        def panel():
            p = rrect((w, h), self.px(24), th["card_b"] + (225,), th["outline"] + (120,), 2)
            return p
        self.paste(canvas, self.cached(("tl_panel", w, h), panel), cx, cy, a * clamp(lt / 0.2))
        rng = np.random.default_rng(7)
        cols = [(80, 110, 200), (70, 150, 110), (150, 80, 160), (170, 90, 80)]
        top = cy - h / 2 + self.px(28)
        for t in range(tracks):
            x = cx - w / 2 + self.px(24)
            k = 0
            right = cx + w / 2 - self.px(24)
            while x < right - self.px(30):
                cw = min(int(rng.integers(self.px(60), self.px(150))), int(right - x))
                p = clamp((lt - 0.05 * t - 0.03 * k) / 0.25)
                picked = (t + k) % 4 == 1 and lt > 1.0 + 0.05 * k
                if p > 0:
                    col = th["gold_t"] if picked else cols[t % 4]
                    blk = self.cached(("clip", cw, picked, t % 4), lambda cw=cw, col=col: rrect((cw, th_h), self.px(6), col + (255,)))
                    self.paste(canvas, blk, x + cw / 2, top + t * (th_h + gap) + th_h / 2, a * p * (1 if picked else 0.75))
                x += cw + self.px(8)
                k += 1
        prog = clamp((lt - 0.4) / 2.2)
        by = cy + h / 2 - self.px(40)
        bw = w - self.px(48)
        self.paste(canvas, self.cached(("bar_bg", bw), lambda: rrect((bw, self.px(10)), self.px(5), (255, 255, 255, 40))), cx, by, a)
        if prog > 0:
            fw = max(self.px(10), int(bw * prog))
            bar = vgrad((fw, self.px(10)), th["gold_t"], th["gold_b"]).convert("RGBA")
            bar.putalpha(rrect((fw, self.px(10)), self.px(5), (0, 0, 0, 255)).getchannel("A"))
            self.paste(canvas, bar, cx - bw / 2 + fw / 2, by, a)
        txt = f"{label_txt or 'AI timeline'}  {int(prog * 100)}%"
        tm = colored(text_mask(txt, font("bold", self.px(24)), pad=0), th["label"])
        self.paste(canvas, tm, cx - bw / 2 + tm.width / 2, by - self.px(28), a)

    def draw_logos(self, canvas, lt, cx, cy, a, items: List[str]):
        names = [i for i in (items or ["Premiere Pro", "DaVinci Resolve", "EDIUS"]) if i][:5]
        n = len(names)
        tw, gap = self.px(240), self.px(36)
        for i, nm in enumerate(names):
            p = clamp((lt - i * 0.22) / 0.35)
            if p <= 0:
                continue
            x = cx + (i - (n - 1) / 2) * (tw + gap)
            card = self.cached(("logo", nm), lambda nm=nm: self.logo_tile(nm, tw))
            self.paste(canvas, card, x, cy + (1 - ease_out(p)) * self.px(40), a * ease_out(p), 0.8 + 0.2 * ease_back(p))

    def logo_tile(self, name: str, tw: int) -> Image.Image:
        th = self.th
        h = int(tw * 0.9)
        out = rrect((tw, h), self.px(22), th["card_t"] + (235,), th["outline"] + (150,), 2)
        d = ImageDraw.Draw(out)
        b = self.px(84)
        bx, by = (tw - b) // 2, self.px(26)
        low = name.lower()
        if "premiere" in low:
            d.rounded_rectangle((bx, by, bx + b, by + b), self.px(16), fill=(18, 0, 50), outline=(153, 153, 255), width=self.px(4))
            d.text((bx + b / 2, by + b / 2), "Pr", font=font("heavy", self.px(40)), fill=(153, 153, 255), anchor="mm")
        elif "davinci" in low or "resolve" in low:
            for j, col in enumerate([(240, 80, 80), (80, 200, 120), (70, 140, 240)]):
                ang = j * 2.094
                ox, oy = math.cos(ang) * b * 0.18, math.sin(ang) * b * 0.18
                d.ellipse((bx + b * 0.28 + ox, by + b * 0.28 + oy, bx + b * 0.72 + ox, by + b * 0.72 + oy), fill=col)
        elif "edius" in low:
            d.rounded_rectangle((bx, by, bx + b, by + b), self.px(16), fill=(10, 30, 60), outline=(90, 170, 255), width=self.px(4))
            d.text((bx + b / 2, by + b / 2), "E", font=font("heavy", self.px(46)), fill=(120, 190, 255), anchor="mm")
        else:
            d.rounded_rectangle((bx, by, bx + b, by + b), self.px(16), outline=th["outline"], width=self.px(4))
            d.text((bx + b / 2, by + b / 2), name[:2].upper(), font=font("heavy", self.px(38)), fill=th["gold_t"], anchor="mm")
        f = font("bold", self.px(28))
        while f.getbbox(name)[2] > tw - self.px(20) and f.size > 12:
            f = font("bold", f.size - 2)
        d.text((tw / 2, by + b + self.px(44)), name, font=f, fill=WHITE, anchor="mm")
        return out

    def draw_clock(self, canvas, lt, cx, cy, a):
        s = self.px(240)
        base = self.cached(("clock", s), lambda: icon("clock", s, self.th["gold_t"]))
        lay = Image.new("RGBA", (s + self.px(80), s + self.px(80)), (0, 0, 0, 0))
        lay.alpha_composite(glow_layer(pad_canvas(base.getchannel("A"), self.px(40)), self.th["glow"], self.px(18), 0.8))
        lay.alpha_composite(base, (self.px(40), self.px(40)))
        d = ImageDraw.Draw(lay)
        c = lay.width / 2
        ang = lt * 5.5
        d.line([(c, c), (c + self.px(78) * math.sin(ang), c - self.px(78) * math.cos(ang))], fill=self.th["gold_t"], width=self.px(12))
        d.ellipse((c - self.px(14), c - self.px(14), c + self.px(14), c + self.px(14)), fill=self.th["gold_t"])
        self.paste(canvas, lay, cx, cy, a * clamp(lt / 0.2), 0.6 + 0.4 * ease_back(clamp(lt / 0.4)))

    def draw_button(self, canvas, lt, cx, cy, a, txt: str):
        btn = self.cached(("button", txt), lambda: self.chip(txt or "JOIN NOW", "ticket", True, 60))
        p = ease_back(clamp(lt / 0.35))
        click = 0.9
        press = math.sin(clamp((lt - click) / 0.22) * math.pi) if lt > click else 0.0
        pulse = 0.03 * math.sin((lt - 1.2) * 6) if lt > 1.2 else 0
        self.paste(canvas, btn, cx, cy, a * clamp(lt / 0.15), (0.5 + 0.5 * p) * (1 - 0.07 * press + pulse))
        if lt > click:
            rr = clamp((lt - click) / 0.5)
            ring = Image.new("RGBA", (self.px(400), self.px(400)), (0, 0, 0, 0))
            R = self.px(40) + self.px(150) * ease_out(rr)
            c = self.px(200)
            ImageDraw.Draw(ring).ellipse((c - R, c - R, c + R, c + R), outline=self.th["gold_t"] + (int(220 * (1 - rr)),), width=self.px(6))
            self.paste(canvas, ring, cx + btn.width * 0.3, cy + self.px(20), a)
        cp = clamp((lt - 0.4) / 0.45)
        if cp > 0:
            cur = self.cached(("cursor",), lambda: icon("cursor", self.px(70), WHITE))
            sx, sy = cx + btn.width * 0.55, cy + self.px(220)
            ex, ey = cx + btn.width * 0.3, cy + self.px(25)
            e = ease_out(cp)
            self.paste(canvas, cur, sx + (ex - sx) * e + self.px(35), sy + (ey - sy) * e + self.px(35), a * e, 1 - 0.12 * press)

    def draw_progress(self, canvas, lt, cx, cy, a, txt: str):
        th = self.th
        w, h = int(self.W * 0.72), self.px(22)
        prog = clamp(lt / 2.0)
        self.paste(canvas, self.cached(("pbg", w), lambda: rrect((w, h), h // 2, (255, 255, 255, 38), th["outline"] + (120,), 2)), cx, cy, a)
        fw = max(h, int(w * ease_out(prog)))
        bar = vgrad((fw, h), th["gold_t"], th["gold_b"]).convert("RGBA")
        bar.putalpha(rrect((fw, h), h // 2, (0, 0, 0, 255)).getchannel("A"))
        self.paste(canvas, bar, cx - w / 2 + fw / 2, cy, a)
        tm = colored(text_mask(f"{txt}  {int(prog * 100)}%" if txt else f"{int(prog * 100)}%", font("bold", self.px(30)), pad=0), WHITE)
        self.paste(canvas, tm, cx, cy - self.px(44), a)

    def lower_third(self, name: str, role: str) -> Image.Image:
        a = self.headline(name.upper(), "", 66)
        b = self.chip(role.upper(), "target", True, 28) if role else None
        out = Image.new("RGBA", (max(a.width, b.width if b else 0) + self.px(20), a.height + (b.height if b else 0)), (0, 0, 0, 0))
        out.alpha_composite(a, (0, 0))
        if b:
            out.alpha_composite(b, (0, a.height - self.px(30)))
        return out

    # ---- elements, scenes, frame
    def element_image(self, el: Dict[str, Any]) -> Optional[Image.Image]:
        t, size = el["type"], el.get("size", "m")
        avail = int(2 * min(el.get("x", 0.5), 1 - el.get("x", 0.5)) * self.W * 0.94)  # text placed off-centre stays on its side
        key = ("el", t, el.get("text"), el.get("text2"), el.get("highlight"), size, el.get("icon"), el.get("gold"), el.get("image"), avail)
        def make():
            if t == "label":
                return self.label(el.get("text", ""), SIZES["label"][size])
            if t == "headline":
                return self.headline(el.get("text", "").upper(), el.get("highlight", ""), SIZES["headline"][size], avail)
            if t == "gold":
                return self.gold_text(el.get("text", "").upper(), SIZES["gold"][size], avail)
            if t == "chip":
                return self.chip(el.get("text", "").upper(), el.get("icon", "spark"), bool(el.get("gold", True)), SIZES["chip"][size])
            if t == "card":
                return self.card(el.get("text2", ""), el.get("text", "").upper(), el.get("icon", "none"))
            if t == "icon_card":
                return self.icon_card(el.get("icon", "spark"), el.get("text", "").upper(), el.get("text2", "").upper())
            if t == "image":
                return self.photo(int(el.get("image", -1)), size)
            if t == "lower_third":
                return self.lower_third(el.get("text", ""), el.get("text2", ""))
            return None
        return self.cached(key, make)

    def draw_element(self, canvas, t, el, exit_a):
        lt = t - float(el["at"])
        if lt < 0:
            return
        cx, cy = el["x"] * self.W, el["y"] * self.H
        kind = el["type"]
        if kind == "grid":
            return self.draw_grid(canvas, lt, cx, cy, exit_a)
        if kind == "timeline":
            return self.draw_timeline(canvas, lt, cx, cy, exit_a, el.get("text", ""))
        if kind == "logos":
            return self.draw_logos(canvas, lt, cx, cy, exit_a, el.get("items") or [])
        if kind == "clock":
            return self.draw_clock(canvas, lt, cx, cy, exit_a)
        if kind == "button":
            return self.draw_button(canvas, lt, cx, cy, exit_a, el.get("text", "").upper())
        if kind == "progress":
            return self.draw_progress(canvas, lt, cx, cy, exit_a, el.get("text", ""))
        im = self.element_image(el)
        if im is None:
            return
        anim = el.get("anim", "rise")
        if anim == "pop":
            self.paste(canvas, im, cx, cy, exit_a * clamp(lt / 0.12), 0.7 + 0.3 * ease_back(clamp(lt / 0.32)))
        elif anim == "slide":
            p = ease_out(clamp(lt / 0.4))
            self.paste(canvas, im, cx - (1 - p) * self.px(140), cy, exit_a * p)
        elif anim == "fade":
            self.paste(canvas, im, cx, cy, exit_a * clamp(lt / 0.3))
        else:  # rise
            p = ease_out(clamp(lt / 0.35))
            self.paste(canvas, im, cx, cy + (1 - p) * self.px(50), exit_a * p)

    def draw_mentor(self, canvas, t, i, sc):
        idx = int(sc.get("image", -1))
        im = self.mentor(idx)
        if im is None:
            return
        s0, s1 = sc["start"], sc["end"]
        lt = t - s0
        prev_has = i > 0 and int(self.scenes[i - 1].get("image", -1)) >= 0
        if prev_has:  # cut from another photo: quick punch-in
            scale, dy, a = 1.06 - 0.06 * ease_out(clamp(lt / 0.25)), 0.0, 1.0
        else:
            p = ease_out(clamp(lt / 0.45))
            scale, dy, a = 1.0, (1 - p) * self.px(160), p
        scale *= 1.0 + 0.035 * clamp(lt / max(0.1, s1 - s0))  # slow push-in
        a *= 1 - clamp((t - s1) / TRANS)
        if i + 1 < len(self.scenes) and int(self.scenes[i + 1].get("image", -1)) >= 0 and t > s1:
            a = 0  # the next scene's photo replaces this one with a cut
        side = sc.get("image_side", "center")
        x = {"left": self.W * 0.27, "right": self.W * 0.73}.get(side, self.W / 2)
        if self.has_alpha(self.asset(idx)):
            self.paste(canvas, im, x, self.H - im.height * scale / 2 + dy + self.px(10), a, scale)
        else:
            self.paste(canvas, im, x, self.H * 0.62 + dy, a, scale)

    @lru_cache(None)
    def background(self) -> Image.Image:
        th, W, H = self.th, self.W, self.H
        y, x = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((x - W * 0.5) / (W * 0.95)) ** 2 + ((y - H * 0.42) / (H * 0.62)) ** 2)
        t = np.clip(d, 0, 1)[..., None] ** 1.3
        img = np.array(th["bg0"]) * (1 - t) + np.array(th["bg1"]) * t
        rng = np.random.default_rng(3)
        img = img + rng.normal(0, 1.6, img.shape[:2])[..., None]
        out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
        if th["stars"]:
            dr = ImageDraw.Draw(out)
            for _ in range(int(W * H / 9000)):
                sx, sy = rng.uniform(0, W), rng.uniform(0, H)
                r = rng.uniform(0.6, 1.8) * self.k
                v = int(rng.uniform(70, 190))
                dr.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(v, int(v * 0.9), int(v * 0.75), 255))
        return out

    @lru_cache(None)
    def bottom_fade(self) -> Image.Image:
        H, W = self.H, self.W
        a = np.zeros((H, W), np.uint8)
        span = int(H * 0.22)
        ramp = np.clip((np.arange(H) - (H - span)) / span, 0, 1) ** 1.6
        a[:] = (ramp * 200)[:, None].astype(np.uint8)
        lay = Image.new("RGBA", (W, H), tuple(self.th["bg1"]) + (0,))
        lay.putalpha(Image.fromarray(a))
        return lay

    @lru_cache(None)
    def light_blob(self) -> Image.Image:
        s = int(900 * self.k)
        m = Image.new("L", (s, s), 0)
        ImageDraw.Draw(m).ellipse((s / 6, s / 6, s * 5 / 6, s * 5 / 6), fill=60)
        return colored(m.filter(ImageFilter.GaussianBlur(int(140 * self.k))), self.th["glow"])

    @lru_cache(None)
    def header(self) -> Optional[Image.Image]:
        """Brand line at the top: logo + name on the left, a badge (e.g. LIVE · ₹21) on the right."""
        name, sub, badge = self.spec.get("brand_name", ""), self.spec.get("brand_sub", ""), self.spec.get("badge", "")
        if not (name or badge or self.logo):
            return None
        out = Image.new("RGBA", (self.W, self.px(110)), (0, 0, 0, 0))
        x = self.px(36)
        if self.logo:
            try:
                with Image.open(self.logo) as lg:
                    lg = lg.convert("RGBA")
                s = self.px(64)
                lg.thumbnail((s, s), Image.LANCZOS)
                out.alpha_composite(lg, (x, (out.height - lg.height) // 2))
                x += lg.width + self.px(16)
            except Exception:
                pass
        if name:
            tm = text_mask(name.upper(), font("heavy", self.px(26)), pad=0)
            out.alpha_composite(colored(tm, WHITE), (x, out.height // 2 - tm.height - self.px(2)))
            if sub:
                sm = text_mask(sub.upper(), font("mono", self.px(16)), spacing=self.px(4), pad=0)
                out.alpha_composite(colored(sm, self.th["label"]), (x, out.height // 2 + self.px(6)))
        if badge:
            f = font("heavy", self.px(22))
            bm = text_mask(badge.upper(), f, pad=0)
            bw, bh = bm.width + self.px(70), self.px(46)
            pill = rrect((bw, bh), bh // 2, (20, 14, 10, 220), self.th["outline"] + (180,), 2)
            ImageDraw.Draw(pill).ellipse((self.px(16), bh // 2 - self.px(7), self.px(30), bh // 2 + self.px(7)), fill=(240, 60, 60))
            pill.alpha_composite(colored(bm, WHITE), (self.px(44), (bh - bm.height) // 2))
            out.alpha_composite(pill, (self.W - bw - self.px(36), (out.height - bh) // 2))
        return out

    @staticmethod
    def _phrases(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Subtitle lines: up to ~4 words / 26 characters, split at pauses."""
        out, cur = [], []
        for i, w in enumerate(words):
            cur.append(w)
            text = " ".join(x["text"] for x in cur)
            nxt = words[i + 1] if i + 1 < len(words) else None
            pause = nxt is not None and nxt["start"] - w["end"] > 0.35
            if len(cur) >= 4 or len(text) >= 26 or pause or nxt is None or re.search(r"[.?!,।]$", w["text"]):
                out.append({"start": cur[0]["start"], "end": (nxt["start"] if nxt and not pause else w["end"] + 0.25), "words": cur})
                cur = []
        return out

    def draw_subtitle(self, canvas, t):
        ph = next((p for p in self.phrases if p["start"] <= t < p["end"]), None)
        if not ph:
            return
        key = ("sub", ph["start"], next((i for i, w in enumerate(ph["words"]) if w["start"] <= t < w["end"] + 0.05), -1))
        def make():
            f = font("heavy", self.px(36))
            active = key[2]
            parts = [(w["text"], i == active) for i, w in enumerate(ph["words"])]
            space = self.px(12)
            widths = [f.getbbox(p)[2] - f.getbbox(p)[0] for p, _ in parts]
            tw = sum(widths) + space * (len(parts) - 1)
            h = self.px(64)
            out = rrect((tw + self.px(44), h), self.px(16), (8, 6, 4, 200), self.th["outline"] + (90,), 2)
            d = ImageDraw.Draw(out)
            x = self.px(22)
            for (p, on), w_ in zip(parts, widths):
                fp = font_for(f, p)
                d.text((x - fp.getbbox(p)[0], h / 2), p, font=fp, fill=self.th["gold_t"] if on else WHITE, anchor="lm")
                x += w_ + space
            return out
        self.paste(canvas, self.cached(key, make), self.W / 2, self.H * 0.915)

    def frame(self, t: float) -> Image.Image:
        canvas = self.background().copy()
        blob = self.light_blob()
        canvas.alpha_composite(blob, (int(self.W / 2 - blob.width / 2 + 260 * self.k * math.sin(t * 0.35)),
                                      int(self.H * 0.3 + 200 * self.k * math.cos(t * 0.27) - blob.height / 2)))
        for i, sc in enumerate(self.scenes):
            if sc["start"] <= t < sc["end"] + TRANS and int(sc.get("image", -1)) >= 0:
                self.draw_mentor(canvas, t, i, sc)
        canvas.alpha_composite(self.bottom_fade())
        for sc in self.scenes:
            if not (sc["start"] <= t < sc["end"] + TRANS):
                continue
            ex = 1 - clamp((t - sc["end"]) / TRANS)
            for el in sc.get("elements", []):
                self.draw_element(canvas, t, el, ex)
            if sc.get("flash") and 0 <= t - sc["start"] < 0.2:
                k = 1 - (t - sc["start"]) / 0.2
                canvas.alpha_composite(Image.new("RGBA", (self.W, self.H), tuple(self.th["gold_t"]) + (int(110 * k * k),)))
        hdr = self.header()
        if hdr is not None:
            canvas.alpha_composite(hdr, (0, 0))
        if self.phrases:
            self.draw_subtitle(canvas, t)
        g = min(clamp(t / 0.25), clamp((self.duration - t) / 0.4))
        out = canvas.convert("RGB")
        if g < 1:
            out = Image.blend(Image.new("RGB", (self.W, self.H)), out, g)
        return out

    def render(self, out_path: str, progress: Optional[Callable[[float], None]] = None) -> str:
        from packages.ai_engine.audio_io import ffmpeg_path
        n = int(self.duration * FPS)
        dur = self.duration
        cmd = [ffmpeg_path(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.W}x{self.H}",
               "-r", str(FPS), "-i", "-"]
        if self.audio and os.path.isfile(self.audio):
            cmd += ["-i", self.audio, "-filter_complex",
                    f"[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,apad,atrim=0:{dur:.2f},afade=t=out:st={max(0, dur - 1.2):.2f}:d=1.2[a]",
                    "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-movflags", "+faststart", "-t", f"{dur:.2f}", out_path]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for i in range(n):
                ff.stdin.write(self.frame(i / FPS).tobytes())
                if progress and i % 15 == 0:
                    progress(i / n)
            ff.stdin.close()
        except BrokenPipeError:
            pass
        err = ff.stderr.read().decode("utf-8", "ignore")
        if ff.wait() != 0 or not os.path.isfile(out_path):
            raise MotionAdError(f"Video encoding failed: {err[-300:]}")
        return out_path


# ---------------------------------------------------------------- spec checks
def normalize_spec(spec: Dict[str, Any], n_assets: int, duration: float) -> Dict[str, Any]:
    """Keeps a spec from Claude inside what the renderer understands: times, positions, types, image numbers."""
    spec = dict(spec)
    spec["duration"] = round(max(3.0, duration), 2)
    spec["theme"] = spec.get("theme") if spec.get("theme") in THEMES else "gold_dark"
    scenes = []
    for sc in sorted(spec.get("scenes") or [], key=lambda s: float(s.get("start", 0))):
        s0 = clamp(float(sc.get("start", 0)), 0, duration)
        s1 = clamp(float(sc.get("end", s0 + 2)), s0 + 0.2, duration)
        img = int(sc.get("image", -1))
        els = []
        for el in sc.get("elements") or []:
            if el.get("type") not in ELEMENT_TYPES:
                continue
            e = dict(el)
            e["at"] = round(clamp(float(e.get("at", s0)), s0, s1), 2)
            e["x"], e["y"] = clamp(float(e.get("x", 0.5)), 0.05, 0.95), clamp(float(e.get("y", 0.4)), 0.06, 0.94)
            e["size"] = e.get("size") if e.get("size") in ("s", "m", "l", "xl") else "m"
            e["icon"] = e.get("icon") if e.get("icon") in ICONS else "none"
            e["anim"] = e.get("anim") if e.get("anim") in ("pop", "rise", "fade", "slide") else "rise"
            if e["type"] == "image" and not (0 <= int(e.get("image", -1)) < n_assets):
                continue
            els.append(e)
        scenes.append({"start": round(s0, 2), "end": round(s1, 2), "image": img if 0 <= img < n_assets else -1,
                       "image_side": sc.get("image_side") if sc.get("image_side") in ("left", "center", "right") else "center",
                       "flash": bool(sc.get("flash")), "elements": els})
    if not scenes:
        raise MotionAdError("Smart AI returned no scenes. Try again with a clearer prompt.")
    scenes[-1]["end"] = spec["duration"]  # the last scene holds to the end
    spec["scenes"] = scenes
    return spec


# ---------------------------------------------------------------- projects: create and edit
def find_assets(folder: str) -> Tuple[List[str], str]:
    """Photos / designs in the folder (sorted) and an optional logo (a file with 'logo' in its name)."""
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(names):
            if not n.startswith(".") and n.lower().endswith(STILL_EXTS):
                files.append(os.path.join(root, n))
    logo = next((f for f in files if "logo" in os.path.basename(f).lower()), "")
    return [f for f in files if f != logo], logo


def _thumb(path: str, out_dir: str, i: int) -> Optional[str]:
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (40, 40, 40, 255))
        bg.alpha_composite(im)
        bg.thumbnail((384, 384))
        p = os.path.join(out_dir, f"asset_{i:02d}.jpg")
        bg.convert("RGB").save(p, "JPEG", quality=80)
        return p
    except Exception:
        return None


class MotionAdProject:
    """A folder holding the words, the specs (v1, v2…) and the videos of one ad."""

    def __init__(self, project_dir: str):
        self.dir = project_dir
        self.file = os.path.join(project_dir, "project.json")
        with open(self.file, encoding="utf-8") as f:
            self.data = json.load(f)

    def save(self):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    @property
    def latest(self) -> Dict[str, Any]:
        return self.data["versions"][-1]

    def spec(self, version: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with open(os.path.join(self.dir, (version or self.latest)["spec"]), encoding="utf-8") as f:
            return json.load(f)


def _render_version(project: MotionAdProject, spec: Dict[str, Any], request: str,
                    log: Callable[[str, str], None], progress: Callable[[float], None]) -> Dict[str, Any]:
    d = project.data
    n = len(d["versions"]) + 1
    spec_name, video_name = f"spec_v{n}.json", f"{d['name']}_v{n}.mp4"
    with open(os.path.join(project.dir, spec_name), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    log(f"🎬 Rendering v{n}: {len(spec['scenes'])} scenes, {spec['duration']:.0f}s at {d['aspect']}…", "info")
    t0 = time.time()
    Renderer(spec, d["assets"], d["audio"], d["aspect"], d["words"], d.get("logo", "")).render(
        os.path.join(project.dir, video_name), progress)
    d["versions"].append({"version": n, "spec": spec_name, "video": video_name, "request": request, "created": time.time()})
    project.save()
    path = os.path.join(project.dir, video_name)
    log(f"✅ Motion-graphics ad v{n} ready ({time.time() - t0:.0f}s): {path}", "info")
    return {"project_dir": project.dir, "video": path, "version": n}


def create(folder: str, audio: str, prompt: str, aspect: str, out_root: str,
           log: Callable[[str, str], None], progress: Callable[[float], None]) -> Dict[str, Any]:
    from packages.ai_engine import ai_audio
    from packages.ai_engine.cloud_ai_client import cloud_ai_client
    assets, logo = find_assets(folder)
    if not assets:
        raise MotionAdError("No photos or designs (PNG, JPG, PSD) in this folder.")
    if not (audio and os.path.isfile(audio)):
        raise MotionAdError("Choose the voiceover (song / audio field): the ad is timed to every spoken word.")
    aspect = aspect if aspect in ASPECTS else "9:16"
    name = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(folder.rstrip("/\\")))[:40] or "Motion_Ad"
    project_dir = os.path.join(out_root, f"{name}_{aspect.replace(':', 'x')}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(project_dir, exist_ok=True)

    log(f"🎙️ Reading the voiceover word by word…", "info")
    tr = ai_audio.transcribe(audio)
    words = tr["words"]
    duration = max(words[-1]["end"] + 1.5, 3.0)
    log(f"  {len(words)} words · {duration:.0f}s: “{tr.get('text', '')[:140]}…”", "normal")
    progress(0.05)

    thumbs = [p for i, a in enumerate(assets[:12]) if (p := _thumb(a, project_dir, i))]
    log(f"🧠 Smart AI is designing the ad: {len(assets)} photos, {len(words)} words, {aspect}…", "info")
    raw = cloud_ai_client.design_motion_ad(words, thumbs, prompt, aspect, duration)
    spec = normalize_spec(raw, len(assets), duration)
    log(f"  “{spec.get('title', 'Motion Ad')}” · {len(spec['scenes'])} scenes · theme {spec['theme']}", "normal")

    data = {"name": name, "folder": folder, "audio": audio, "aspect": aspect, "prompt": prompt, "assets": assets,
            "logo": logo, "thumbs": thumbs, "words": words, "duration": duration, "versions": []}
    with open(os.path.join(project_dir, "project.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    project = MotionAdProject(project_dir)
    return _render_version(project, spec, prompt, log, lambda p: progress(0.1 + 0.9 * p))


def edit(project_dir: str, request: str, log: Callable[[str, str], None], progress: Callable[[float], None]) -> Dict[str, Any]:
    from packages.ai_engine.cloud_ai_client import cloud_ai_client
    project = MotionAdProject(project_dir)
    d = project.data
    request = (request or "").strip()
    if not request:
        raise MotionAdError("Write what to change, e.g. “scene 2 headline bada karo, gold me”.")
    log(f"✏️ Smart AI is editing v{project.latest['version']}: “{request}”", "info")
    raw = cloud_ai_client.design_motion_ad(d["words"], d["thumbs"], d["prompt"], d["aspect"], d["duration"],
                                           current_spec=project.spec(), edit_request=request)
    spec = normalize_spec(raw, len(d["assets"]), d["duration"])
    return _render_version(project, spec, request, log, progress)
