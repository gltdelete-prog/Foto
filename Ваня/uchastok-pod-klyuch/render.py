"""Сборка рекламного ролика «Участок под ключ» (9:16, 1080x1920, 30 fps, без звука).

Кадры сцен лежат в scenes/, надписи накладываются программно, чтобы русский текст
и номер телефона были идеально читаемыми.

Запуск:  python3 render.py            -> uchastok-pod-klyuch.mp4
         python3 render.py --preview  -> preview/*.jpg (ключевые кадры)
"""

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
SCENES = ROOT / "scenes"
FONTS = ROOT / "fonts"
OUT = ROOT / "uchastok-pod-klyuch.mp4"

W, H = 1080, 1920
FPS = 30
DURATION = 20.5
XF = 0.4  # длительность перекрёстного перехода между сценами
SRC_SCALE = 1.25  # запас разрешения под наезды камеры

PHONE = "89995511488"

WHITE = (255, 255, 255)
GREEN = (74, 222, 88)
DARK = (10, 14, 10)
GREY = (200, 208, 200)

F_HEAD = str(FONTS / "NotoSansDisplay-Bold.ttf")
F_BOLD = str(FONTS / "Inter-Bold.ttf")
F_SEMI = str(FONTS / "Inter-SemiBold.ttf")


def font(path, size):
    return ImageFont.truetype(path, size)


def fit_font(path, text, size, max_w):
    f = font(path, size)
    while f.getlength(text) > max_w and size > 20:
        size -= 2
        f = font(path, size)
    return f


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def lerp(a, b, p):
    return a + (b - a) * p


# ---------------------------------------------------------------- изображения

def load_source(name):
    img = Image.open(SCENES / name).convert("RGB")
    return img.resize((int(W * SRC_SCALE), int(H * SRC_SCALE)), Image.LANCZOS)


def camera(src, zoom, cx, cy, shake=0.0, t=0.0):
    """Виртуальная камера: zoom >= 1, центр кадра (cx, cy) в долях исходника."""
    sw, sh = src.size
    cw = min(sw / zoom, sw)
    ch = cw * H / W
    x = cx * sw + shake * (math.sin(t * 7.3) * 0.6 + math.sin(t * 13.1) * 0.4)
    y = cy * sh + shake * (math.cos(t * 6.1) * 0.6 + math.sin(t * 11.7) * 0.4)
    x0 = min(max(x - cw / 2, 0), sw - cw)
    y0 = min(max(y - ch / 2, 0), sh - ch)
    frame = src.transform((W, H), Image.EXTENT, (x0, y0, x0 + cw, y0 + ch), Image.BILINEAR)
    return np.asarray(frame, dtype=np.float32)


def make_vignette(strength=0.35):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    v = 1 - strength * np.clip(d, 0, 1) ** 2.2
    return v[..., None]


def make_gradient(height, max_alpha, from_top):
    """Тёмная подложка под текст (RGBA)."""
    g = np.zeros((height, W, 4), dtype=np.float32)
    ramp = np.linspace(0, 1, height, dtype=np.float32)
    if from_top:
        ramp = ramp[::-1]
    g[..., 3] = (ramp[:, None] ** 1.3) * max_alpha
    return g


# ---------------------------------------------------------------- текстовые блоки

def text_layer(lines, width=W, line_gap=10, pad=30, shadow=True):
    """lines: [(text, font, color)], ("bar", w, h, color) или ("gap", h). Всё по центру."""
    heights = []
    for item in lines:
        if item[0] == "bar":
            heights.append(item[2])
        elif item[0] == "gap":
            heights.append(item[1])
        else:
            asc, desc = item[1].getmetrics()
            heights.append(asc + desc)
    total_h = sum(heights) + line_gap * (len(lines) - 1) + pad * 2
    layer = Image.new("RGBA", (width, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = pad
    for item, h in zip(lines, heights):
        if item[0] == "bar":
            _, bw, bh, color = item
            x = (width - bw) // 2
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=bh // 2, fill=color)
        elif item[0] != "gap":
            text, f, color = item
            d.text(((width - f.getlength(text)) / 2, y), text, font=f, fill=color)
        y += h + line_gap
    if shadow:
        a = layer.split()[3]
        soft = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        soft.putalpha(a.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.75)))
        hard = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        hard.putalpha(a.filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.6)))
        layer = Image.alpha_composite(Image.alpha_composite(soft, hard), layer)
    return np.asarray(layer, dtype=np.float32) / 255.0


def pill(text, bg, fg, size=42):
    f = font(F_BOLD, size)
    tw = int(f.getlength(text))
    asc, desc = f.getmetrics()
    w, h = tw + 56, asc + desc + 22
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=bg)
    d.text((28, 9), text, font=f, fill=fg)
    return np.asarray(img, dtype=np.float32) / 255.0


def composite(frame, layer, x, y, alpha=1.0):
    if alpha <= 0.001:
        return
    lh, lw = layer.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + lw, W), min(y + lh, H)
    if x1 <= x0 or y1 <= y0:
        return
    sub = layer[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sub[..., 3:4] * alpha
    region = frame[y0:y1, x0:x1]
    region[:] = region * (1 - a) + sub[..., :3] * 255.0 * a


def anim(t, t_in, t_out, dur_in=0.35, dur_out=0.25):
    """(alpha, сдвиг по y) для появления снизу и плавного исчезновения надписи."""
    if t < t_in or t > t_out:
        return 0.0, 0.0
    p_in = ease_out((t - t_in) / dur_in)
    p_out = 1 - ease_in_out((t - (t_out - dur_out)) / dur_out) if t > t_out - dur_out else 1.0
    return p_in * p_out, (1 - p_in) * 46


def title_block(text_lines, size=112):
    lines = [("bar", 110, 10, GREEN), ("gap", 8)]
    for line in text_lines:
        lines.append((line, fit_font(F_HEAD, line, size, W - 120), WHITE))
    return text_layer(lines, line_gap=6)


# ---------------------------------------------------------------- сборка

def build():
    src = {
        "before": load_source("scene1_before.jpg"),
        "trimmer": load_source("scene2_trimmer.jpg"),
        "tree": load_source("scene3_tree.jpg"),
        "branches": load_source("scene4_branches.jpg"),
        "roots": load_source("scene5_roots.jpg"),
        "after": load_source("scene6_after.jpg"),
    }

    # фон финального экрана: почти чёрная размытая фактура травы
    fin_src = Image.open(SCENES / "scene2_trimmer.jpg").convert("RGB")
    fin_src = fin_src.resize((int(W * 1.15), int(H * 1.15)), Image.LANCZOS)
    fin_src = fin_src.filter(ImageFilter.GaussianBlur(18))
    final_bg = np.asarray(fin_src, dtype=np.float32) * 0.16

    vignette = make_vignette()
    grad_bottom = make_gradient(900, 0.82, from_top=False)
    grad_top = make_gradient(820, 0.62, from_top=True)

    # ---- надписи
    s1_label = pill("ДО", (12, 14, 12, 230), WHITE)
    s1_title = text_layer([
        ("УЧАСТОК ЗАРОС?", fit_font(F_HEAD, "УЧАСТОК ЗАРОС?", 128, W - 110), WHITE),
    ])
    s1_sub = text_layer([
        ("bar", 110, 10, GREEN), ("gap", 4),
        ("ПРИВЕДЁМ В ПОРЯДОК", fit_font(F_HEAD, "ПРИВЕДЁМ В ПОРЯДОК", 78, W - 120), GREEN),
    ])
    t_grass = title_block(["ПОКОС ТРАВЫ"])
    t_tree = title_block(["СПИЛ ДЕРЕВЬЕВ"])
    t_branch = title_block(["СБОР И ВЫВОЗ", "ВЕТОК"])
    t_roots = title_block(["ВЫКОРЧЁВЫВАНИЕ", "КОРНЕЙ"])
    lbl_before = pill("ДО", (12, 14, 12, 235), WHITE, 46)
    lbl_after = pill("ПОСЛЕ", GREEN + (255,), DARK, 46)
    t_was = text_layer([
        ("БЫЛО → СТАЛО", fit_font(F_BOLD, "БЫЛО → СТАЛО", 108, W - 120), WHITE),
    ])
    t_turnkey = text_layer([
        ("bar", 110, 10, GREEN), ("gap", 8),
        ("УЧАСТОК", font(F_HEAD, 124), WHITE),
        ("ПОД КЛЮЧ", font(F_HEAD, 124), GREEN),
    ], line_gap=0)

    fin_head = text_layer([("ВЕРНЁМ УЧАСТКУ ПОРЯДОК",
                            fit_font(F_HEAD, "ВЕРНЁМ УЧАСТКУ ПОРЯДОК", 84, W - 110), WHITE)])
    fin_line = text_layer([("bar", 520, 6, GREEN)], pad=10, shadow=False)
    fin_call = text_layer([("ЗВОНИТЕ ПРЯМО СЕЙЧАС", font(F_SEMI, 58), GREEN)])
    fin_phone = text_layer([(PHONE, fit_font(F_BOLD, PHONE, 190, W - 90), WHITE)])
    srv1 = "ПОКОС ТРАВЫ  •  СПИЛ ДЕРЕВЬЕВ"
    srv2 = "ВЫВОЗ ВЕТОК  •  ВЫКОРЧЁВЫВАНИЕ КОРНЕЙ"
    fin_srv = text_layer([
        (srv1, fit_font(F_SEMI, srv1, 38, W - 100), GREY),
        (srv2, fit_font(F_SEMI, srv2, 38, W - 100), GREY),
    ], line_gap=14)

    title_bottom = 1610  # нижняя граница заголовков: выше зоны интерфейса историй

    def place_bottom(layer):
        return title_bottom - layer.shape[0]

    # ---- сцены
    def sc_before(t, p):
        return camera(src["before"], lerp(1.0, 1.16, ease_in_out(p)), 0.5, lerp(0.56, 0.6, p))

    def sc_trimmer(t, p):
        return camera(src["trimmer"], 1.18, lerp(0.40, 0.58, ease_in_out(p)), 0.58, shake=5, t=t)

    def sc_tree(t, p):
        return camera(src["tree"], lerp(1.04, 1.2, ease_in_out(p)), lerp(0.5, 0.56, p),
                      lerp(0.55, 0.62, p), shake=3, t=t)

    def sc_branches(t, p):
        return camera(src["branches"], lerp(1.22, 1.04, ease_in_out(p)), lerp(0.45, 0.5, p),
                      0.55, shake=3, t=t)

    def sc_roots(t, p):
        return camera(src["roots"], lerp(1.02, 1.16, ease_in_out(p)), 0.5,
                      lerp(0.62, 0.52, ease_in_out(p)), shake=4, t=t)

    def split_pos(t):
        # шторка проезжает справа налево и останавливается посередине кадра
        return lerp(W + 10, W * 0.5, ease_in_out((t - 15.15) / 0.8))

    def sc_compare(t, p):
        z = lerp(1.04, 1.1, p)
        out = camera(src["before"], z, 0.5, 0.58)
        after = camera(src["after"], z, 0.5, 0.58)
        sx = int(split_pos(t))
        if sx < W:
            out[:, max(sx, 0):] = after[:, max(sx, 0):]
            g0, g1 = max(sx - 14, 0), min(sx + 14, W)
            out[:, g0:g1] = out[:, g0:g1] * 0.6 + np.array(GREEN, np.float32) * 0.4
            out[:, max(sx - 3, 0):min(sx + 3, W)] = 255.0
        return out

    def sc_final(t, p):
        z = lerp(1.0, 1.06, p)
        bh, bw = final_bg.shape[:2]
        cw = int(min(bw, W * 1.15 / z))
        ch = int(cw * H / W)
        x0, y0 = (bw - cw) // 2, (bh - ch) // 2
        crop = final_bg[y0:y0 + ch, x0:x0 + cw]
        img = Image.fromarray(crop.astype(np.uint8)).resize((W, H), Image.BILINEAR)
        return np.asarray(img, dtype=np.float32)

    scenes = [
        (0.0, 3.0, sc_before),
        (3.0, 6.0, sc_trimmer),
        (6.0, 9.0, sc_tree),
        (9.0, 12.0, sc_branches),
        (12.0, 15.0, sc_roots),
        (15.0, 17.0, sc_compare),
        (17.0, DURATION, sc_final),
    ]

    def background(t):
        frames = []
        for i, (s, e, fn) in enumerate(scenes):
            lo = s - (XF / 2 if i > 0 else 0)
            hi = e + (XF / 2 if i < len(scenes) - 1 else 0)
            if lo <= t <= hi:
                w = ease_in_out((t - lo) / XF) if i > 0 and t < s + XF / 2 else 1.0
                frames.append((fn(t, (t - lo) / (hi - lo)), w))
        if len(frames) == 1:
            return frames[0][0]
        (a, _), (b, wb) = frames[0], frames[-1]
        return a * (1 - wb) + b * wb

    def overlays(frame, t):
        if t < 3.1:
            a, _ = anim(t, 0.15, 3.0, 0.5, 0.3)
            composite(frame, grad_top, 0, 0, a)
        if 3.0 <= t < 17.1:
            a = 1.0
            if t < 3.4:
                a = ease_in_out((t - 3.0) / 0.4)
            if t >= 15.0:
                a = lerp(1.0, 0.7, ease_in_out((t - 15.0) / 0.4))
            if t >= 16.8:
                a *= 1 - ease_in_out((t - 16.8) / 0.3)
            composite(frame, grad_bottom, 0, H - grad_bottom.shape[0], a)

        # 1. проблема / ДО
        a, dy = anim(t, 0.2, 2.95)
        composite(frame, s1_label, 60, 280 + dy, a)
        a, dy = anim(t, 0.35, 2.95)
        composite(frame, s1_title, 0, 360 + dy, a)
        a, dy = anim(t, 0.85, 2.95)
        composite(frame, s1_sub, 0, 360 + s1_title.shape[0] - 20 + dy, a)

        # 2–5. услуги
        for layer, s in ((t_grass, 3.0), (t_tree, 6.0), (t_branch, 9.0), (t_roots, 12.0)):
            a, dy = anim(t, s + 0.25, s + 2.9)
            composite(frame, layer, 0, place_bottom(layer) + dy, a)

        # 6. до / после
        if 15.0 <= t < 17.2:
            fade = 1 - ease_in_out((t - 16.8) / 0.3) if t > 16.8 else 1.0
            a, dy = anim(t, 15.05, 17.1)
            composite(frame, lbl_before, 60, 280 + dy, a * fade)
            if split_pos(t) < W - 40:
                a2 = ease_out((t - 15.6) / 0.35)
                composite(frame, lbl_after, W - 60 - lbl_after.shape[1], 280, a2 * fade)
        a, dy = anim(t, 15.25, 16.2, 0.3, 0.2)
        composite(frame, t_was, 0, place_bottom(t_was) + dy, a)
        a, dy = anim(t, 16.15, 17.05, 0.3, 0.2)
        composite(frame, t_turnkey, 0, place_bottom(t_turnkey) + dy, a)

        # 7. финальный экран
        if t >= 17.0:
            y = 560
            a, dy = anim(t, 17.1, DURATION + 1)
            composite(frame, fin_head, 0, y + dy, a)
            y += fin_head.shape[0] + 10
            grow = ease_out((t - 17.25) / 0.5)
            if grow > 0:
                ln = fin_line.copy()
                half = int((520 / 2 + 10) * grow)
                ln[:, W // 2 + half:] = 0
                ln[:, :W // 2 - half] = 0
                composite(frame, ln, 0, y)
            y += fin_line.shape[0] + 40
            a, dy = anim(t, 17.2, DURATION + 1)
            composite(frame, fin_call, 0, y + dy, a)
            y += fin_call.shape[0] + 10
            a, dy = anim(t, 17.2, DURATION + 1, 0.3)
            composite(frame, fin_phone, 0, y + dy, a)
            y += fin_phone.shape[0] + 70
            a, dy = anim(t, 17.4, DURATION + 1)
            composite(frame, fin_srv, 0, y + dy, a)
        return frame

    def render(t):
        frame = background(t)
        frame = frame * vignette if t < 17.0 else frame * (0.6 + 0.4 * vignette)
        frame = overlays(frame, t)
        return np.clip(frame, 0, 255).astype(np.uint8)

    return render


def main():
    render = build()
    if "--preview" in sys.argv:
        out = ROOT / "preview"
        out.mkdir(exist_ok=True)
        for t in (1.5, 2.8, 4.5, 7.5, 10.5, 13.5, 15.6, 16.6, 18.5, 20.4):
            Image.fromarray(render(t)).save(out / f"frame_{t:05.2f}.jpg", quality=90)
        print("preview saved to", out)
        return

    n = int(round(DURATION * FPS))
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-movflags", "+faststart", str(OUT),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        proc.stdin.write(render(i / FPS).tobytes())
        if i % 60 == 0:
            print(f"frame {i}/{n}", flush=True)
    proc.stdin.close()
    proc.wait()
    print("saved", OUT)


if __name__ == "__main__":
    main()
