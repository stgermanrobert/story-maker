#!/usr/bin/env python3
"""
Story Maker - video horizontal a vertical con fondo desenfocado.
Requiere: Python 3.8+ (con Tkinter, viene incluido en Windows) y FFmpeg/FFprobe en el PATH.
Opcional: yt-dlp para descargar desde YouTube.
"""
import base64
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox

APP_NAME = "Story Maker"
APP_VERSION = "1.0.4"
SNAP_DURATIONS = (15, 30, 60)   # duraciones "imán" en segundos
SNAP_PX = 9                      # radio del imán en píxeles (con zoom se vuelve más fino)
ALT_MASK = 0x20000 if os.name == "nt" else 0x0008
CTRL_MASK = 0x0004
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "StoryMaker"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DOWNLOADS = Path.home() / "Downloads"
TEMP_DIR = Path(tempfile.gettempdir()) / "storymaker"
PLAY_FPS_MAX = 30

# Versión portable: si hay una carpeta "bin" junto al programa (ffmpeg, ffprobe, ffplay, yt-dlp),
# se usa esa antes que lo que haya instalado en el sistema.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
_BIN = APP_DIR / "bin"
if _BIN.is_dir():
    os.environ["PATH"] = str(_BIN) + os.pathsep + os.environ.get("PATH", "")

FORMATS = {
    "Story / Reel 9:16 (1080x1920)": (1080, 1920),
    "Feed vertical 4:5 (1080x1350)": (1080, 1350),
    "Cuadrado 1:1 (1080x1080)": (1080, 1080),
    "Story 720p (720x1280)": (720, 1280),
}
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]

DEFAULTS = {
    "format": "Story / Reel 9:16 (1080x1920)",
    # Recorte de la fuente (afecta fondo y frente)
    "crop_x": 0.0, "crop_y": 0.0,
    # Fondo
    "bg_zoom": 1.0, "bg_pan_x": 50.0, "bg_pan_y": 50.0, "bg_blur": 40.0,
    "bg_brightness": -0.08, "bg_contrast": 1.0, "bg_saturation": 1.0,
    "bg_opacity": 100.0, "bg_color": "#000000", "bg_mirror": False, "bg_vignette": False,
    # Video principal
    "fg_width": 100.0, "fg_offset_x": 0.0, "fg_offset_y": 0.0, "fg_opacity": 100.0,
    "fg_crop_l": 0.0, "fg_crop_r": 0.0, "fg_crop_t": 0.0, "fg_crop_b": 0.0,
    "fg_radius": 0.0, "fg_border": 0.0, "fg_border_color": "#ffffff",
    "fg_shadow": 0.0, "fg_shadow_opacity": 60.0,
    # Exportación
    "segment": False, "segment_len": 60.0,
    "crf": 18.0, "preset": "slow", "audio_bitrate": "192k",
    "loop": False, "snap": True,
}


# ---------------------------------------------------------------- utilidades
def even(x):
    return max(2, int(round(x / 2.0)) * 2)


def hex_ff(color):
    return "0x" + color.lstrip("#")


def parse_time(text):
    """Acepta '75', '75.5', '1:15', '1:21.200', '0:01:15'. Devuelve segundos."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        raise ValueError("vacío")
    secs = 0.0
    for p in text.split(":"):
        secs = secs * 60 + float(p)
    return secs


def fmt_ms(secs):
    secs = max(0.0, secs or 0.0)
    ms = int(round(secs * 1000))
    m, rest = divmod(ms, 60000)
    s, ms = divmod(rest, 1000)
    return f"{m}:{s:02d}.{ms:03d}"


def fmt_short(secs, step=1.0):
    m, s = divmod(max(0.0, secs), 60)
    if step < 1:
        return f"{int(m)}:{s:04.1f}"
    return f"{int(m)}:{int(s):02d}"


def find_tool(name):
    return shutil.which(name)


def ps_quote(arg):
    if arg and re.fullmatch(r"[\w\-.:\\/=+@%]+", arg):
        return arg
    return '"' + arg.replace("`", "``").replace('"', '`"').replace("$", "`$") + '"'


def glob_escape(text):
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def run_quiet(args, **kw):
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, **kw)


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe no pudo leer el archivo")
    data = json.loads(out.stdout)
    info = {"width": None, "height": None, "has_audio": False, "fps": 30.0,
            "duration": float(data.get("format", {}).get("duration") or 0)}
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and info["width"] is None:
            info["width"], info["height"] = st.get("width"), st.get("height")
            try:
                n, d = (st.get("avg_frame_rate") or "30/1").split("/")
                if float(d) > 0 and float(n) > 0:
                    info["fps"] = float(n) / float(d)
            except ValueError:
                pass
        elif st.get("codec_type") == "audio":
            info["has_audio"] = True
    if not info["width"]:
        raise RuntimeError("El archivo no tiene pista de video")
    return info


# ---------------------------------------------------------------- geometría y filtro
def fg_geometry(s, src_w, src_h, k=1.0):
    """Todas las medidas del lienzo y del video principal, escaladas por k."""
    W0, H0 = FORMATS[s["format"]]
    W, H = even(W0 * k), even(H0 * k)
    cw = even(src_w * (1 - 2 * s["crop_x"] / 100.0))
    ch = even(src_h * (1 - 2 * s["crop_y"] / 100.0))
    l, r = s["fg_crop_l"], s["fg_crop_r"]
    t, b = s["fg_crop_t"], s["fg_crop_b"]
    if l + r > 90:
        l, r = l * 90 / (l + r), r * 90 / (l + r)
    if t + b > 90:
        t, b = t * 90 / (t + b), b * 90 / (t + b)
    fcw = even(cw * (1 - (l + r) / 100.0))
    fch = even(ch * (1 - (t + b) / 100.0))
    fcx = int(cw * l / 100.0)
    fcy = int(ch * t / 100.0)
    fw = even(W * s["fg_width"] / 100.0)
    fh = even(fch * fw / fcw)
    bd = int(round(s["fg_border"] * k))
    if s["fg_border"] > 0:
        bd = max(1, bd)
    gw, gh = fw + 2 * bd, fh + 2 * bd
    x = (W - gw) / 2.0 + W * s["fg_offset_x"] / 100.0
    y = (H - gh) / 2.0 + H * s["fg_offset_y"] / 100.0
    return dict(W=W, H=H, cw=cw, ch=ch, fcw=fcw, fch=fch, fcx=fcx, fcy=fcy,
                fw=fw, fh=fh, bd=bd, gw=gw, gh=gh, x=x, y=y)


def build_filter(s, src_w, src_h, k=1.0, fps=None, out_fmt="yuv420p"):
    """filter_complex completo; k escala todo (para preview/reproducción rápidas). Salida en [v]."""
    G = fg_geometry(s, src_w, src_h, k)
    W, H = G["W"], G["H"]
    g = [f"[0:v]crop={G['cw']}:{G['ch']},setsar=1,split=2[src_bg][src_fg]"]

    # Fondo
    z = s["bg_zoom"]
    px, py = s["bg_pan_x"] / 100.0, s["bg_pan_y"] / 100.0
    bg = [f"scale={even(W * z)}:{even(H * z)}:force_original_aspect_ratio=increase",
          f"crop={W}:{H}:(iw-{W})*{px:.4f}:(ih-{H})*{py:.4f}"]
    if s["bg_mirror"]:
        bg.append("hflip")
    sigma = s["bg_blur"] * k
    if sigma > 0.3:
        f = 4 if sigma >= 16 else (2 if sigma >= 6 else 1)
        if f > 1:
            bg.append(f"scale={even(W / f)}:{even(H / f)}")
        bg.append(f"gblur=sigma={sigma / f:.2f}")
        if f > 1:
            bg.append(f"scale={W}:{H}")
    if (s["bg_brightness"], s["bg_contrast"], s["bg_saturation"]) != (0, 1, 1):
        bg.append(f"eq=brightness={s['bg_brightness']:.3f}:contrast={s['bg_contrast']:.3f}"
                  f":saturation={s['bg_saturation']:.3f}")
    if s["bg_vignette"]:
        bg.append("vignette=PI/4")
    op_bg = s["bg_opacity"] / 100.0
    if op_bg < 1:
        bg.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={hex_ff(s['bg_color'])}@{1 - op_bg:.3f}:t=fill")
    g.append("[src_bg]" + ",".join(bg) + "[bg]")

    # Video principal
    fg = []
    if (G["fcw"], G["fch"]) != (G["cw"], G["ch"]):
        fg.append(f"crop={G['fcw']}:{G['fch']}:{G['fcx']}:{G['fcy']}")
    fg.append(f"scale={G['fw']}:{G['fh']}")
    bd = G["bd"]
    if bd > 0:
        fg.append(f"pad=iw+{2 * bd}:ih+{2 * bd}:{bd}:{bd}:color={hex_ff(s['fg_border_color'])}")
    gw, gh = G["gw"], G["gh"]
    radius = min(int(round(s["fg_radius"] * k)), gw // 2, gh // 2)
    op_fg = s["fg_opacity"] / 100.0
    shadow = int(round(s["fg_shadow"] * k))
    if s["fg_shadow"] > 0:
        shadow = max(1, shadow)
    if radius > 0 or op_fg < 1 or shadow > 0:
        fg.append("format=rgba")
    g.append("[src_fg]" + ",".join(fg) + "[fg0]")

    fg_label = "fg0"
    if radius > 0 or op_fg < 1:
        a = f"{255 * op_fg:.2f}"
        if radius > 0:
            dx = f"(abs(X-W/2+0.5)-(W/2-{radius}))"
            dy = f"(abs(Y-H/2+0.5)-(H/2-{radius}))"
            expr = f"{a}*if(gt({dx},0)*gt({dy},0),clip({radius}+0.5-hypot({dx},{dy}),0,1),1)"
        else:
            expr = a
        g.append(f"color=c=white:s={gw}x{gh}:r=1:d=1,format=gray,geq=lum='{expr}'[mask]")
        g.append("[fg0][mask]alphamerge[fg1]")
        fg_label = "fg1"

    x, y = int(round(G["x"])), int(round(G["y"]))
    base = "bg"
    if shadow > 0:
        so = s["fg_shadow_opacity"] / 100.0
        p = 2 * shadow
        g.append(f"[{fg_label}]split=2[fgm][fgs]")
        g.append(f"[fgs]colorchannelmixer=rr=0:gg=0:bb=0:aa={so:.3f},"
                 f"pad=iw+{2 * p}:ih+{2 * p}:{p}:{p}:color=black@0,gblur=sigma={shadow}[sh]")
        g.append(f"[bg][sh]overlay=x={x - p}:y={y - p + shadow // 2}[bgs]")
        base, fg_label = "bgs", "fgm"

    tail = [f"format={out_fmt}"]
    if fps:
        tail.insert(0, f"fps={fps:.3f}")
    g.append(f"[{base}][{fg_label}]overlay=x={x}:y={y}," + ",".join(tail) + "[v]")
    return ";".join(g)


# ---------------------------------------------------------------- tooltip
class Tooltip:
    """Globo de ayuda al dejar el mouse quieto sobre un widget."""
    def __init__(self, widget, text, delay=450):
        self.w, self.text, self.delay = widget, text, delay
        self.tip = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, e=None):
        self._hide()
        self.job = self.w.after(self.delay, self._show)

    def _show(self):
        x = self.w.winfo_rootx() + 4
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", bg="#2a2a37", fg="#e8e8f0",
                 relief="solid", bd=1, padx=6, pady=3).pack()

    def _hide(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------- línea de tiempo
class Timeline(tk.Canvas):
    PAD = 14
    RULER = 20
    BAR_TOP = 23      # barra del bloque (se arrastra para mover entrada+salida juntas)
    BAR_H = 14
    STEPS = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]

    def __init__(self, master, app, colors, height=122):
        super().__init__(master, height=height, bg=colors["panel"], highlightthickness=0, cursor="hand2")
        self.app, self.c = app, colors
        self.dur = 0.0
        self.v0, self.v1 = 0.0, 1.0
        self.t_in = self.t_out = self.ph = 0.0
        self.wave = None
        self.wave_key = None
        self.drag = None
        self.snap_hit = None     # (tiempo, etiqueta) cuando el imán engancha
        self.block = None        # estado mientras se arrastra el bloque
        self.hover_bar = False
        self.bind("<Configure>", lambda e: (self.redraw(), app._request_wave()))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", lambda e: self.reset_zoom())
        self.bind("<Motion>", self._hover)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Shift-MouseWheel>", lambda e: self._pan(-1 if e.delta > 0 else 1))
        self.bind("<Button-4>", lambda e: self._zoom(e.x, 0.8))
        self.bind("<Button-5>", lambda e: self._zoom(e.x, 1.25))

    # coordenadas
    def wpx(self):
        return max(10, self.winfo_width() - 2 * self.PAD)

    def t2x(self, t):
        return self.PAD + (t - self.v0) / max(self.v1 - self.v0, 1e-6) * self.wpx()

    def x2t(self, x):
        t = self.v0 + (x - self.PAD) / self.wpx() * (self.v1 - self.v0)
        return min(self.dur, max(0.0, t))

    def wave_area(self):
        h = self.winfo_height()
        top = self.BAR_TOP + self.BAR_H + 4
        return top, max(10, h - top - 6)

    def x2t_free(self, x):
        """Como x2t pero sin limitar al rango del video (para arrastres suaves)."""
        return self.v0 + (x - self.PAD) / self.wpx() * (self.v1 - self.v0)

    def view_key(self):
        return (round(self.v0, 3), round(self.v1, 3), self.wpx(), self.wave_area()[1])

    def set_media(self, dur):
        self.dur = max(dur, 0.1)
        self.v0, self.v1 = 0.0, self.dur
        self.t_in, self.t_out, self.ph = 0.0, self.dur, 0.0
        self.wave = self.wave_key = None
        self.redraw()

    def reset_zoom(self):
        if self.dur:
            self.v0, self.v1 = 0.0, self.dur
            self.redraw()
            self.app._request_wave()

    def ensure_visible(self, t):
        span = self.v1 - self.v0
        if span >= self.dur - 1e-6:
            return
        if t > self.v1 or t < self.v0:
            self.v0 = min(max(0.0, t - span * 0.1), self.dur - span)
            self.v1 = self.v0 + span
            self.app._request_wave()

    # dibujo
    def redraw(self):
        self.delete("all")
        c = self.c
        w, h = self.winfo_width(), self.winfo_height()
        top, wh = self.wave_area()
        self.create_rectangle(self.PAD, top - 2, w - self.PAD, top + wh + 2, fill=c["bg"], outline="")
        if not self.dur:
            self.create_text(w // 2, h // 2 + 8, text="Línea de tiempo", fill=c["dim"], font=self.app.font)
            return
        if self.wave is not None and self.wave_key == self.view_key():
            self.create_image(self.PAD, top, anchor="nw", image=self.wave)

        span = self.v1 - self.v0
        raw = span * 80 / self.wpx()
        step = next((s for s in self.STEPS if s >= raw), self.STEPS[-1])
        t = math.ceil(self.v0 / step) * step
        while t <= self.v1 + 1e-9:
            x = self.t2x(t)
            self.create_line(x, self.RULER - 6, x, self.RULER, fill=c["dim"])
            self.create_text(x + 3, 2, anchor="nw", text=fmt_short(t, step), fill=c["dim"],
                             font=(self.app.font[0], 8))
            t += step

        xi, xo = self.t2x(self.t_in), self.t2x(self.t_out)
        dim = dict(fill="#000000", stipple="gray50", outline="")
        if xi > self.PAD:
            self.create_rectangle(self.PAD, top - 2, min(xi, w - self.PAD), top + wh + 2, **dim)
        if xo < w - self.PAD:
            self.create_rectangle(max(xo, self.PAD), top - 2, w - self.PAD, top + wh + 2, **dim)
        bx0, bx1 = max(xi, self.PAD), min(xo, w - self.PAD)
        if bx1 > bx0:
            active = self.drag == "block" or self.hover_bar
            self.create_rectangle(bx0, self.BAR_TOP, bx1, self.BAR_TOP + self.BAR_H,
                                  fill=c["accent2"] if active else c["accent"], outline="")
            if bx1 - bx0 > 120:
                self.create_text((bx0 + bx1) / 2, self.BAR_TOP + self.BAR_H / 2,
                                 text=f"◀  {fmt_ms(self.t_out - self.t_in)}  ▶", fill="#ffffff",
                                 font=(self.app.font[0], 8, "bold"))
            elif bx1 - bx0 > 24:
                self.create_text((bx0 + bx1) / 2, self.BAR_TOP + self.BAR_H / 2, text="◀▶",
                                 fill="#ffffff", font=(self.app.font[0], 7))
        for x, col, side in ((xi, c["in"], 1), (xo, c["out"], -1)):
            if self.PAD - 8 <= x <= w - self.PAD + 8:
                self.create_line(x, self.BAR_TOP, x, top + wh + 2, fill=col, width=2)
                self.create_polygon(x, top - 2, x + 9 * side, top - 2, x, top + 9, fill=col, outline="")
                self.create_polygon(x, top + wh + 2, x + 9 * side, top + wh + 2, x, top + wh - 9,
                                    fill=col, outline="")
        # guías del imán mientras se arrastra entrada/salida
        if self.drag in ("in", "out") and self.app.vars["snap"].get():
            for tt, lab in self.snap_targets(self.drag):
                if lab == "cursor":
                    continue
                gx = self.t2x(tt)
                if self.PAD <= gx <= w - self.PAD:
                    self.create_line(gx, top, gx, top + wh, fill=c["dim"], dash=(2, 4))
                    self.create_text(gx + 3, top + wh - 2, anchor="sw", text=lab, fill=c["dim"],
                                     font=(self.app.font[0], 8))
        if self.snap_hit:
            sx = self.t2x(self.snap_hit[0])
            self.create_line(sx, top - 4, sx, top + wh + 2, fill="#ffd166", width=2)
            tx = min(max(sx, self.PAD + 30), w - self.PAD - 30)
            self.create_rectangle(tx - 28, top + 8, tx + 28, top + 26, fill="#ffd166", outline="")
            self.create_text(tx, top + 17, text="◆ " + self.snap_hit[1], fill="#16161d",
                             font=(self.app.font[0], 8, "bold"))
        xp = self.t2x(self.ph)
        if self.PAD - 2 <= xp <= w - self.PAD + 2:
            self.create_line(xp, self.RULER - 2, xp, top + wh + 2, fill="#ffffff", width=1)
            self.create_polygon(xp - 6, self.RULER - 8, xp + 6, self.RULER - 8, xp, self.RULER,
                                fill="#ffffff", outline="")
        if span < self.dur - 1e-6:
            # barra de posición del zoom
            a = self.PAD + self.v0 / self.dur * self.wpx()
            b = self.PAD + self.v1 / self.dur * self.wpx()
            self.create_rectangle(a, h - 3, b, h, fill=c["border"], outline="")

    # interacción
    def _in_bar(self, y):
        return self.BAR_TOP - 2 <= y <= self.BAR_TOP + self.BAR_H + 2

    def _hit(self, x, y=0, state=0):
        xi, xo = self.t2x(self.t_in), self.t2x(self.t_out)
        di, do = abs(x - xi), abs(x - xo)
        if min(di, do) <= 7:
            if di < do or (di == do and x < xi):
                return "in"
            return "out"
        if xi < x < xo and (self._in_bar(y) or state & CTRL_MASK):
            return "block"
        return "ph"

    def _hover(self, e):
        if not self.dur:
            return
        hit = self._hit(e.x, e.y, e.state)
        self.configure(cursor={"in": "sb_h_double_arrow", "out": "sb_h_double_arrow",
                               "block": "fleur"}.get(hit, "hand2"))
        hb = hit == "block"
        if hb != self.hover_bar:
            self.hover_bar = hb
            self.redraw()

    def _press(self, e):
        if not self.dur:
            return
        self.drag = self._hit(e.x, e.y, e.state)
        self.app._tl_press(self.drag)
        if self.drag == "block":
            self.block = dict(grab=self.x2t_free(e.x) - self.t_in, length=self.t_out - self.t_in)
            self.redraw()
            return
        self._motion(e)

    def snap_targets(self, kind):
        """Puntos a los que se 'pega' cada cosa que se arrastra."""
        tg = []
        if kind == "out":
            tg += [(self.t_in + d, f"{d} s") for d in SNAP_DURATIONS]
            tg.append((self.ph, "cursor"))
        elif kind == "in":
            tg += [(self.t_out - d, f"{d} s") for d in SNAP_DURATIONS]
            tg.append((self.ph, "cursor"))
        else:
            tg += [(self.t_in, "entrada"), (self.t_out, "salida")]
        return [(t, lab) for t, lab in tg if 0 <= t <= self.dur]

    def _snap(self, kind, t, state):
        self.snap_hit = None
        if not self.app.vars["snap"].get() or (state & ALT_MASK):
            return t
        x = self.t2x(t)
        best = None
        for tt, lab in self.snap_targets(kind):
            d = abs(self.t2x(tt) - x)
            if d <= SNAP_PX and (best is None or d < best[0]):
                best = (d, tt, lab)
        if best:
            self.snap_hit = (best[1], best[2])
            return best[1]
        return t

    def _snap_block(self, new_in, length, state):
        """Imán para el bloque: cualquiera de sus bordes se pega al cursor, al inicio o al final."""
        self.snap_hit = None
        if not self.app.vars["snap"].get() or (state & ALT_MASK):
            return new_in
        targets = [(self.ph, "cursor"), (0.0, "inicio"), (self.dur, "final")]
        best = None
        for edge in (0.0, length):
            ex = self.t2x(new_in + edge)
            for tt, lab in targets:
                d = abs(self.t2x(tt) - ex)
                if d <= SNAP_PX and (best is None or d < best[0]):
                    best = (d, tt - edge, tt, lab)
        if best:
            self.snap_hit = (best[2], best[3])
            return best[1]
        return new_in

    def _motion(self, e):
        if not self.drag:
            return
        if self.drag == "block":
            b = self.block
            new_in = self.x2t_free(e.x) - b["grab"]
            new_in = self._snap_block(new_in, b["length"], e.state)
            new_in = min(max(0.0, new_in), self.dur - b["length"])
            self.app._move_block(new_in, b["length"])
            # auto-desplazamiento si el bloque se acerca al borde con zoom
            span = self.v1 - self.v0
            if span < self.dur - 1e-6:
                if e.x > self.winfo_width() - self.PAD and self.v1 < self.dur:
                    self.v0 = min(self.dur - span, self.v0 + span * 0.02)
                    self.v1 = self.v0 + span
                    self.app._request_wave()
                elif e.x < self.PAD and self.v0 > 0:
                    self.v0 = max(0.0, self.v0 - span * 0.02)
                    self.v1 = self.v0 + span
                    self.app._request_wave()
            return
        t = self._snap(self.drag, self.x2t(e.x), e.state)
        if self.drag == "in":
            self.app._set_in(min(t, self.t_out - 0.05))
        elif self.drag == "out":
            self.app._set_out(max(t, self.t_in + 0.05))
        else:
            self.app._seek(t, live=True)

    def _release(self, e):
        if self.drag:
            d, self.drag = self.drag, None
            self.snap_hit = None
            self.block = None
            self.redraw()
            self.app._tl_release(d)

    def _wheel(self, e):
        self._zoom(e.x, 0.8 if e.delta > 0 else 1.25)

    def _zoom(self, x, factor):
        if not self.dur:
            return
        tc = self.x2t(x)
        span = self.v1 - self.v0
        new = min(self.dur, max(0.5, span * factor))
        v0 = tc - (tc - self.v0) * new / span
        v0 = min(max(0.0, v0), self.dur - new)
        self.v0, self.v1 = v0, v0 + new
        self.redraw()
        self.app._request_wave()

    def _pan(self, direction):
        if not self.dur:
            return
        span = self.v1 - self.v0
        v0 = min(max(0.0, self.v0 + direction * span * 0.15), self.dur - span)
        self.v0, self.v1 = v0, v0 + span
        self.redraw()
        self.app._request_wave()


# ---------------------------------------------------------------- app
class StoryMaker:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} v{APP_VERSION}")
        self.PREVIEW_H = max(360, min(640, root.winfo_screenheight() - 530))
        self.q = queue.Queue()
        self.settings = dict(DEFAULTS)
        self.settings.update(self._load_settings())
        if self.settings.get("format") not in FORMATS:
            self.settings["format"] = DEFAULTS["format"]
        self.vars, self._refresh, self._swatches = {}, {}, []
        self.vars["format"] = tk.StringVar(value=self.settings["format"])
        self.video = self.audio = self.info = None
        self.proc = self.dl_proc = None
        self._pv_after = self._save_after = self._wave_after = self._restart_after = None
        self._pv_gen = self._wave_gen = 0
        self._photo = None
        self.last_output = None
        # reproducción
        self.playing = False
        self.play_gen = 0
        self.vproc = None
        self.aproc = None
        self.wav_path = None
        self.play = {}
        self.edit = None

        self._style()
        self._build_ui()
        self._check_tools()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(50, self._poll)

    # ------------------------------------------------ estilo
    def _style(self):
        self.c = dict(bg="#16161d", panel="#1f1f29", fg="#e8e8f0", dim="#9a9ab0",
                      accent="#7c5cff", accent2="#9b82ff", field="#2a2a37", border="#3a3a52",
                      **{"in": "#3ddc84", "out": "#ff5c7a"})
        c = self.c
        self.root.configure(bg=c["bg"])
        st = ttk.Style(self.root)
        st.theme_use("clam")
        self.font = ("Segoe UI", 10) if IS_WIN else ("DejaVu Sans", 10)
        f = self.font[0]
        st.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"],
                     bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                     troughcolor=c["field"], font=self.font)
        st.configure("TFrame", background=c["bg"])
        st.configure("Panel.TFrame", background=c["panel"])
        st.configure("TLabel", background=c["bg"], foreground=c["fg"])
        st.configure("Panel.TLabel", background=c["panel"])
        st.configure("Dim.TLabel", background=c["panel"], foreground=c["dim"])
        st.configure("DimBg.TLabel", background=c["bg"], foreground=c["dim"])
        st.configure("Time.TLabel", background=c["panel"], foreground=c["fg"], font=("Consolas", 12) if IS_WIN else ("DejaVu Sans Mono", 11))
        st.configure("Head.TLabel", background=c["panel"], foreground=c["accent2"], font=(f, 10, "bold"))
        st.configure("Title.TLabel", background=c["bg"], font=(f, 15, "bold"))
        st.configure("In.TLabel", background=c["panel"], foreground=c["in"], font=(f, 10, "bold"))
        st.configure("Out.TLabel", background=c["panel"], foreground=c["out"], font=(f, 10, "bold"))
        st.configure("TNotebook", background=c["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=c["field"], foreground=c["dim"], padding=(14, 6))
        st.map("TNotebook.Tab", background=[("selected", c["panel"]), ("active", c["border"])],
               foreground=[("selected", c["fg"])],
               lightcolor=[("selected", c["panel"])], bordercolor=[("selected", c["border"])])
        st.configure("TButton", background=c["field"], foreground=c["fg"], padding=(10, 5))
        st.map("TButton", background=[("active", c["border"]), ("disabled", c["bg"])],
               foreground=[("disabled", c["dim"])])
        st.configure("Tool.TButton", padding=(6, 3))
        st.configure("Accent.TButton", background=c["accent"], foreground="#ffffff",
                     font=(f, 10, "bold"), padding=(14, 7))
        st.map("Accent.TButton", background=[("active", c["accent2"]), ("disabled", c["field"])])
        st.configure("Play.TButton", background=c["accent"], foreground="#ffffff",
                     font=(f, 11, "bold"), padding=(12, 3))
        st.map("Play.TButton", background=[("active", c["accent2"])])
        st.configure("TCheckbutton", background=c["panel"], foreground=c["fg"])
        st.map("TCheckbutton", background=[("active", c["panel"])],
               indicatorcolor=[("selected", c["accent"]), ("!selected", c["field"])])
        st.configure("Horizontal.TScale", background=c["accent"], troughcolor=c["field"])
        st.map("Horizontal.TScale", background=[("active", c["accent2"])])
        st.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"], insertcolor=c["fg"])
        st.configure("TCombobox", fieldbackground=c["field"], foreground=c["fg"],
                     background=c["field"], arrowcolor=c["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["fg"])])
        self.root.option_add("*TCombobox*Listbox.background", c["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        st.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["field"])

    # ------------------------------------------------ UI
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # Preview
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        ttk.Label(left, text=f"{APP_NAME}  v{APP_VERSION}", style="Title.TLabel").pack(anchor="w")
        self.lbl_file = ttk.Label(left, text="Ningún video cargado", style="DimBg.TLabel")
        self.lbl_file.pack(anchor="w", pady=(0, 8))
        self.canvas = tk.Canvas(left, highlightthickness=1, highlightbackground=self.c["border"], bg="#000000")
        self.canvas.pack()
        self._resize_canvas()
        self._canvas_msg("Cargá un video o pegá un link de YouTube")
        ttk.Label(left, text="Arrastrá el video para moverlo. Esquinas o rueda del mouse para escalar. "
                             "Doble clic lo centra.", style="DimBg.TLabel", justify="left",
                  wraplength=int(self.canvas["width"])).pack(anchor="w", pady=(6, 0))
        self.canvas.bind("<Motion>", self._cv_hover)
        self.canvas.bind("<ButtonPress-1>", self._cv_press)
        self.canvas.bind("<B1-Motion>", self._cv_motion)
        self.canvas.bind("<ButtonRelease-1>", self._cv_release)
        self.canvas.bind("<Double-Button-1>", self._cv_center)
        self.canvas.bind("<MouseWheel>", lambda e: self._cv_wheel(1 if e.delta > 0 else -1))
        self.canvas.bind("<Button-4>", lambda e: self._cv_wheel(1))
        self.canvas.bind("<Button-5>", lambda e: self._cv_wheel(-1))

        # Controles
        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_sources(right)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True, pady=(10, 0))
        self._build_bg_tab(self._tab(nb, "Fondo"))
        self._build_fg_tab(self._tab(nb, "Video principal"))
        self._build_export_tab(self._tab(nb, "Exportación"))

        # Línea de tiempo
        tlbox = ttk.Frame(outer, style="Panel.TFrame", padding=(10, 8))
        tlbox.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._build_transport(tlbox)
        self.timeline = Timeline(tlbox, self, self.c)
        self.timeline.pack(fill="x", pady=(8, 0))
        ttk.Label(tlbox, text="Marcadores verde / rojo = entrada / salida · barra violeta (o Ctrl+arrastrar) = mover el bloque · "
                              "rueda = zoom · Shift+rueda = desplazar · doble clic = ver todo · I / O = marcar · "
                              "Alt al arrastrar = sin imán", style="Dim.TLabel").pack(anchor="w", pady=(4, 0))

        # Exportar
        bottom = ttk.Frame(outer)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        row = ttk.Frame(bottom)
        row.pack(fill="x", pady=(6, 0))
        self.lbl_status = ttk.Label(row, text="", style="DimBg.TLabel")
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.btn_folder = ttk.Button(row, text="Abrir carpeta", command=self._open_folder)
        ttk.Button(row, text="Copiar comando", command=self._copy_command).pack(side="right", padx=(6, 0))
        self.btn_render = ttk.Button(row, text="Exportar", style="Accent.TButton", command=self._render_toggle)
        self.btn_render.pack(side="right", padx=(6, 0))

    def _build_transport(self, p):
        row = ttk.Frame(p, style="Panel.TFrame")
        row.pack(fill="x")
        def b(text, cmd, tip, w=None, padx=(4, 0), style="Tool.TButton"):
            btn = ttk.Button(row, text=text, style=style, command=cmd, width=w)
            btn.pack(side="left", padx=padx)
            Tooltip(btn, tip)
            return btn
        b("|◀", self._go_video_start, "Inicio del video  (Ctrl+Inicio)", 4, padx=(0, 0))
        b("[◀", self._go_block_start, "Inicio del bloque  (Inicio)", 4)
        b("−1 s", lambda: self._step(-1.0), "Retroceder 1 segundo  (Shift+←)", 5)
        b("−1 f", lambda: self._step_frames(-1), "Frame anterior  (←)", 5)
        b("■", self._stop_button, "Stop: vuelve al inicio del bloque.\nOtra vez: al inicio del video.  (Esc)", 3, padx=(10, 0))
        self.btn_play = b("▶  Play", self._toggle_play, "Play / pausa  (Espacio)", 9, padx=(4, 10), style="Play.TButton")
        b("+1 f", lambda: self._step_frames(1), "Frame siguiente  (→)", 5, padx=(0, 0))
        b("+1 s", lambda: self._step(1.0), "Avanzar 1 segundo  (Shift+→)", 5)
        b("▶]", self._go_block_end, "Fin del bloque  (Fin)", 4)
        b("▶|", self._go_video_end, "Fin del video  (Ctrl+Fin)", 4)
        self.lbl_time = ttk.Label(row, text="0:00.000", style="Time.TLabel", width=10)
        self.lbl_time.pack(side="left", padx=(14, 0))
        self.vars["loop"] = tk.BooleanVar(value=self.settings["loop"])
        ttk.Checkbutton(row, text="Loop", variable=self.vars["loop"],
                        command=lambda: self._changed(preview=False)).pack(side="left", padx=(8, 0))

        # entrada / salida (segunda fila)
        row2 = ttk.Frame(p, style="Panel.TFrame")
        row2.pack(fill="x", pady=(8, 0))
        self.in_txt = tk.StringVar()
        self.out_txt = tk.StringVar()
        for label, style, var, mark, setter in (
                ("ENTRADA", "In.TLabel", self.in_txt, "Marcar aquí (I)", "_set_in"),
                ("SALIDA", "Out.TLabel", self.out_txt, "Marcar aquí (O)", "_set_out")):
            ttk.Label(row2, text=label, style=style).pack(side="left", padx=(0, 6))
            e = ttk.Entry(row2, textvariable=var, width=10, justify="center")
            e.pack(side="left")
            e.bind("<Return>", lambda ev, s=setter, v=var: self._typed_mark(s, v))
            e.bind("<FocusOut>", lambda ev, s=setter, v=var: self._typed_mark(s, v))
            ttk.Button(row2, text=mark, style="Tool.TButton",
                       command=lambda s=setter: getattr(self, s)(self.timeline.ph, commit=True)).pack(side="left", padx=(4, 18))
        self.lbl_len = ttk.Label(row2, text="", style="Dim.TLabel", width=17)
        self.lbl_len.pack(side="left")
        for d in SNAP_DURATIONS:
            ttk.Button(row2, text=f"{d} s", style="Tool.TButton", width=5,
                       command=lambda d=d: self._set_length(d)).pack(side="left", padx=(4, 0))
        self.vars["snap"] = tk.BooleanVar(value=self.settings["snap"])
        ttk.Checkbutton(row2, text="Imán", variable=self.vars["snap"],
                        command=lambda: self._changed(preview=False)).pack(side="left", padx=(14, 0))
        ttk.Button(row2, text="Ver todo", style="Tool.TButton",
                   command=lambda: self.timeline.reset_zoom()).pack(side="right")
        ttk.Button(row2, text="Zoom a la selección", style="Tool.TButton",
                   command=self._zoom_selection).pack(side="right", padx=(0, 4))

    def _zoom_selection(self):
        tl = self.timeline
        if not tl.dur:
            return
        pad = max(0.25, (tl.t_out - tl.t_in) * 0.08)
        tl.v0 = max(0.0, tl.t_in - pad)
        tl.v1 = min(tl.dur, tl.t_out + pad)
        tl.redraw()
        self._request_wave()

    def _tab(self, nb, title):
        """Pestaña con scroll vertical (por si los controles no entran en la altura)."""
        holder = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(holder, text=title)
        cv = tk.Canvas(holder, bg=self.c["panel"], highlightthickness=0, height=200)
        sb = ttk.Scrollbar(holder, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv, style="Panel.TFrame", padding=(14, 6, 14, 14))
        win = cv.create_window(0, 0, anchor="nw", window=inner)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)

        def sync(ev=None):
            cv.itemconfigure(win, width=cv.winfo_width())
            need = inner.winfo_reqheight() > cv.winfo_height()
            cv.configure(scrollregion=(0, 0, cv.winfo_width(), inner.winfo_reqheight()))
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y")
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()
                cv.yview_moveto(0)

        inner.bind("<Configure>", sync)
        cv.bind("<Configure>", sync)

        def wheel(ev):
            if sb.winfo_ismapped():
                d = -1 if (getattr(ev, "delta", 0) > 0 or getattr(ev, "num", 0) == 4) else 1
                cv.yview_scroll(d * 2, "units")
            return "break"

        def bind_wheel(ev):
            cv.bind_all("<MouseWheel>", wheel)
            cv.bind_all("<Button-4>", wheel)
            cv.bind_all("<Button-5>", wheel)

        def unbind_wheel(ev):
            cv.unbind_all("<MouseWheel>")
            cv.unbind_all("<Button-4>")
            cv.unbind_all("<Button-5>")

        holder.bind("<Enter>", bind_wheel)
        holder.bind("<Leave>", unbind_wheel)
        return inner

    def _build_sources(self, parent):
        box = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        box.pack(fill="x")
        ttk.Label(box, text="ARCHIVOS", style="Head.TLabel").grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Label(box, text="YouTube", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.url_var = tk.StringVar()
        e = ttk.Entry(box, textvariable=self.url_var)
        e.grid(row=1, column=1, sticky="ew", padx=6)
        e.bind("<Return>", lambda ev: self._download())
        self.btn_dl = ttk.Button(box, text="Bajar 1080p", command=self._download)
        self.btn_dl.grid(row=1, column=2, sticky="ew")
        ttk.Label(box, text="Video", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.lbl_video = ttk.Label(box, text="—", style="Dim.TLabel")
        self.lbl_video.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(box, text="Elegir…", command=self._pick_video).grid(row=2, column=2, sticky="ew")
        ttk.Label(box, text="Audio aparte", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.lbl_audio = ttk.Label(box, text="(opcional — usa el audio del video)", style="Dim.TLabel")
        self.lbl_audio.grid(row=3, column=1, sticky="w", padx=6)
        af = ttk.Frame(box, style="Panel.TFrame")
        af.grid(row=3, column=2, sticky="ew")
        ttk.Button(af, text="Elegir…", command=self._pick_audio).pack(side="left", fill="x", expand=True)
        ttk.Button(af, text="✕", width=3, command=self._clear_audio).pack(side="left", padx=(4, 0))
        ttk.Label(box, text="Formato", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        cb = ttk.Combobox(box, textvariable=self.vars["format"], values=list(FORMATS), state="readonly", width=34)
        cb.grid(row=4, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda ev: self._on_format())
        box.columnconfigure(1, weight=1)

    def _section(self, parent, text):
        ttk.Label(parent, text=text.upper(), style="Head.TLabel").pack(anchor="w", pady=(8, 2))

    def _slider(self, parent, key, label, lo, hi, res, decimals=0, unit=""):
        var = tk.DoubleVar(value=self.settings[key])
        self.vars[key] = var
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=1)
        lab = ttk.Label(row, text=label, style="Panel.TLabel", width=22)
        lab.pack(side="left")
        txt = tk.StringVar()
        ent = ttk.Entry(row, textvariable=txt, width=7, justify="right")
        ent.pack(side="right")
        ttk.Label(row, text=unit, style="Dim.TLabel", width=3).pack(side="right")

        def show():
            txt.set(f"{var.get():.{decimals}f}")

        def snap(v):
            val = min(hi, max(lo, round(float(v) / res) * res))
            if abs(val - var.get()) > 1e-9:
                var.set(val)
            show()
            self._changed()

        def typed(ev=None):
            try:
                val = float(txt.get().replace(",", "."))
            except ValueError:
                show()
                return
            if abs(val - var.get()) > 1e-9:
                var.set(min(hi, max(lo, val)))
                self._changed()
            show()

        def reset(ev=None):
            var.set(DEFAULTS[key])
            show()
            self._changed()

        ttk.Scale(row, from_=lo, to=hi, variable=var, command=snap).pack(side="left", fill="x", expand=True, padx=8)
        ent.bind("<Return>", typed)
        ent.bind("<FocusOut>", typed)
        lab.bind("<Double-Button-1>", reset)
        show()
        self._refresh[key] = show

    def _check(self, parent, key, label):
        var = tk.BooleanVar(value=self.settings[key])
        self.vars[key] = var
        ttk.Checkbutton(parent, text=label, variable=var, command=self._changed).pack(anchor="w", pady=2)

    def _color(self, parent, key, label):
        var = tk.StringVar(value=self.settings[key])
        self.vars[key] = var
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, style="Panel.TLabel", width=22).pack(side="left")
        sw = tk.Label(row, width=4, bg=var.get(), cursor="hand2", highlightthickness=1,
                      highlightbackground=self.c["border"])
        sw.pack(side="left", padx=8)

        def pick(ev=None):
            col = colorchooser.askcolor(color=var.get(), parent=self.root)[1]
            if col:
                var.set(col)
                sw.configure(bg=col)
                self._changed()

        sw.bind("<Button-1>", pick)
        self._swatches.append((sw, var))

    def _hint(self, parent, text):
        ttk.Label(parent, text=text, style="Dim.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(6, 0))

    def _build_bg_tab(self, t):
        self._section(t, "Encuadre")
        self._slider(t, "bg_zoom", "Zoom", 1.0, 3.0, 0.05, 2, "x")
        self._slider(t, "bg_pan_x", "Posición horizontal", 0, 100, 1, 0, "%")
        self._slider(t, "bg_pan_y", "Posición vertical", 0, 100, 1, 0, "%")
        self._check(t, "bg_mirror", "Espejar fondo")
        self._section(t, "Imagen")
        self._slider(t, "bg_blur", "Desenfoque", 0, 120, 1, 0)
        self._slider(t, "bg_brightness", "Brillo", -0.5, 0.5, 0.01, 2)
        self._slider(t, "bg_contrast", "Contraste", 0.5, 2.0, 0.05, 2)
        self._slider(t, "bg_saturation", "Saturación", 0.0, 3.0, 0.05, 2)
        self._check(t, "bg_vignette", "Viñeta (oscurece los bordes)")
        self._section(t, "Opacidad")
        self._slider(t, "bg_opacity", "Opacidad del fondo", 0, 100, 1, 0, "%")
        self._color(t, "bg_color", "Color debajo del fondo")
        self._hint(t, "Doble clic en el nombre de un parámetro lo vuelve a su valor por defecto.")

    def _build_fg_tab(self, t):
        self._section(t, "Tamaño y posición")
        self._slider(t, "fg_width", "Ancho", 20, 250, 1, 0, "%")
        self._slider(t, "fg_offset_x", "Desplazar horizontal", -60, 60, 0.5, 1, "%")
        self._slider(t, "fg_offset_y", "Desplazar vertical", -60, 60, 0.5, 1, "%")
        self._section(t, "Recortar el video principal")
        self._slider(t, "fg_crop_l", "Izquierda", 0, 45, 0.5, 1, "%")
        self._slider(t, "fg_crop_r", "Derecha", 0, 45, 0.5, 1, "%")
        self._slider(t, "fg_crop_t", "Arriba", 0, 45, 0.5, 1, "%")
        self._slider(t, "fg_crop_b", "Abajo", 0, 45, 0.5, 1, "%")
        self._section(t, "Estilo")
        self._slider(t, "fg_opacity", "Opacidad", 0, 100, 1, 0, "%")
        self._slider(t, "fg_radius", "Esquinas redondeadas", 0, 200, 1, 0, "px")
        self._slider(t, "fg_border", "Borde", 0, 40, 1, 0, "px")
        self._color(t, "fg_border_color", "Color del borde")
        self._slider(t, "fg_shadow", "Sombra", 0, 80, 1, 0, "px")
        self._slider(t, "fg_shadow_opacity", "Intensidad de sombra", 0, 100, 1, 0, "%")

    def _build_export_tab(self, t):
        self._section(t, "Recortar barras negras de la fuente")
        self._slider(t, "crop_y", "Arriba y abajo", 0, 25, 0.5, 1, "%")
        self._slider(t, "crop_x", "Costados", 0, 25, 0.5, 1, "%")
        self._hint(t, "Afecta al fondo y al video principal. El fragmento a exportar se elige "
                      "con la entrada y salida de la línea de tiempo.")
        self._section(t, "Partes")
        self._check(t, "segment", "Partir en partes (para subir como varias Stories)")
        self._slider(t, "segment_len", "Duración de cada parte", 5, 120, 1, 0, "s")
        self._section(t, "Calidad")
        self._slider(t, "crf", "CRF (menos = mejor)", 14, 30, 1, 0)
        row = ttk.Frame(t, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Preset x264", style="Panel.TLabel", width=22).pack(side="left")
        self.vars["preset"] = tk.StringVar(value=self.settings["preset"])
        cb = ttk.Combobox(row, textvariable=self.vars["preset"], values=PRESETS, state="readonly", width=12)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda ev: self._changed(preview=False))
        ttk.Label(row, text="Audio", style="Panel.TLabel").pack(side="left", padx=(12, 0))
        self.vars["audio_bitrate"] = tk.StringVar(value=self.settings["audio_bitrate"])
        cb2 = ttk.Combobox(row, textvariable=self.vars["audio_bitrate"],
                           values=["128k", "192k", "256k", "320k"], state="readonly", width=6)
        cb2.pack(side="left", padx=8)
        cb2.bind("<<ComboboxSelected>>", lambda ev: self._changed(preview=False))
        self._section(t, "Salida")
        row = ttk.Frame(t, style="Panel.TFrame")
        row.pack(fill="x", pady=3)
        self.out_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Guardar como…", command=self._pick_output).pack(side="left", padx=(6, 0))
        ttk.Button(t, text="Restablecer todos los parámetros", command=self._reset_all).pack(anchor="w", pady=(14, 0))

    def _bind_keys(self):
        # Botones, checks y sliders sin foco de teclado: así Espacio y las flechas
        # siempre controlan la reproducción y no el último botón tocado.
        def walk(w):
            for ch in w.winfo_children():
                if ch.winfo_class() in ("TButton", "TCheckbutton", "TScale", "TNotebook"):
                    try:
                        ch.configure(takefocus=0)
                    except tk.TclError:
                        pass
                walk(ch)
        walk(self.root)
        self.root.bind_all("<Button-1>", lambda e: self._defocus(e), add="+")

        def guard(fn):
            def h(ev):
                if isinstance(ev.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                    return
                fn(ev)
                return "break"
            return h
        r = self.root
        r.bind_all("<space>", guard(lambda e: self._toggle_play()))
        r.bind_all("<KeyPress-i>", guard(lambda e: self._set_in(self.timeline.ph, commit=True)))
        r.bind_all("<KeyPress-o>", guard(lambda e: self._set_out(self.timeline.ph, commit=True)))
        r.bind_all("<Left>", guard(lambda e: self._step_frames(-1)))
        r.bind_all("<Right>", guard(lambda e: self._step_frames(1)))
        r.bind_all("<Shift-Left>", guard(lambda e: self._step(-1.0)))
        r.bind_all("<Shift-Right>", guard(lambda e: self._step(1.0)))
        r.bind_all("<Home>", guard(lambda e: self._go_block_start()))
        r.bind_all("<End>", guard(lambda e: self._go_block_end()))
        r.bind_all("<Control-Home>", guard(lambda e: self._go_video_start()))
        r.bind_all("<Control-End>", guard(lambda e: self._go_video_end()))
        r.bind_all("<Escape>", guard(lambda e: self._stop_button()))

    def _defocus(self, e):
        w = e.widget
        if isinstance(w, str) or isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        try:
            self.root.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------ settings
    def _current(self):
        return {k: v.get() for k, v in self.vars.items()}

    def _changed(self, preview=True):
        if self._save_after:
            self.root.after_cancel(self._save_after)
        self._save_after = self.root.after(800, self._save_settings)
        if preview:
            self._draw_overlay()
            if self.playing:
                self._schedule_restart()
            else:
                self._schedule_preview()

    def _load_settings(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if k in DEFAULTS}
        except Exception:
            return {}

    def _save_settings(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._current(), indent=2), encoding="utf-8")
        except Exception:
            pass

    def _set_var(self, key, val):
        self.vars[key].set(val)
        if key in self._refresh:
            self._refresh[key]()

    def _reset_all(self):
        for k, v in self.vars.items():
            if k in DEFAULTS:
                v.set(DEFAULTS[k])
        for fn in self._refresh.values():
            fn()
        for sw, var in self._swatches:
            sw.configure(bg=var.get())
        self._on_format()

    def _check_tools(self):
        missing = [t for t in ("ffmpeg", "ffprobe") if not find_tool(t)]
        if missing:
            messagebox.showerror(APP_NAME, "No encuentro " + " ni ".join(missing) +
                                 " en el PATH.\nInstalalo con:  winget install --id Gyan.FFmpeg -e")

    # ------------------------------------------------ archivos
    def _pick_video(self):
        p = filedialog.askopenfilename(title="Elegir video", initialdir=str(DOWNLOADS),
                                       filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"), ("Todos", "*.*")])
        if p:
            self._load_video(Path(p))

    def _pick_audio(self):
        p = filedialog.askopenfilename(
            title="Elegir audio", initialdir=str(self.video.parent if self.video else DOWNLOADS),
            filetypes=[("Audio", "*.webm *.m4a *.mp3 *.opus *.aac *.wav *.ogg *.mp4"), ("Todos", "*.*")])
        if p:
            self.audio = Path(p)
            self.lbl_audio.configure(text=self._short(self.audio.name))
            self._request_wave()

    def _clear_audio(self):
        self.audio = None
        self.lbl_audio.configure(text="(opcional — usa el audio del video)")
        self._request_wave()

    def _short(self, name, n=56):
        return name if len(name) <= n else name[: n - 1] + "…"

    def _audio_src(self):
        if self.audio:
            return self.audio
        if self.info and self.info["has_audio"]:
            return self.video
        return None

    def _load_video(self, path):
        try:
            info = probe(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No pude leer el video:\n{e}")
            return
        self._stop_play()
        self.video, self.info = path, info
        self.lbl_video.configure(text=self._short(path.name))
        self.lbl_file.configure(text=f"{info['width']}x{info['height']} · {info['fps']:.2f} fps · "
                                     f"{fmt_ms(info['duration'])}")
        self.audio = None
        self.lbl_audio.configure(text="(opcional — usa el audio del video)")
        if not info["has_audio"]:
            stem = re.sub(r"\.f\d+$", "", path.stem)
            for cand in sorted(path.parent.glob(glob_escape(stem) + ".f*.*")):
                if cand != path and cand.suffix.lower() in (".webm", ".m4a", ".opus", ".mp3"):
                    self.audio = cand
                    self.lbl_audio.configure(text=self._short(cand.name) + "  (detectado)")
                    break
            else:
                self.lbl_audio.configure(text="⚠ Este video no tiene audio — elegí el audio aparte")
        clean = re.sub(r"\.f\d+$", "", path.stem)
        self.out_var.set(str(path.parent / f"{clean}_story.mp4"))
        self.timeline.set_media(info["duration"])
        self._update_marks()
        self._seek(info["duration"] * 0.3)
        self._draw_overlay()
        self._request_wave()

    def _pick_output(self):
        initial = Path(self.out_var.get()) if self.out_var.get() else DOWNLOADS / "story.mp4"
        p = filedialog.asksaveasfilename(title="Guardar como", defaultextension=".mp4",
                                         initialdir=str(initial.parent), initialfile=initial.name,
                                         filetypes=[("MP4", "*.mp4")])
        if p:
            self.out_var.set(p)

    # ------------------------------------------------ entrada / salida / búsqueda
    def _update_marks(self):
        tl = self.timeline
        self.in_txt.set(fmt_ms(tl.t_in))
        self.out_txt.set(fmt_ms(tl.t_out))
        length = tl.t_out - tl.t_in
        exact = any(abs(length - d) < 0.0005 for d in SNAP_DURATIONS)
        self.lbl_len.configure(text=f"Duración {fmt_ms(length)}" + ("  ✓" if exact else ""),
                               style="In.TLabel" if exact else "Dim.TLabel")
        tl.redraw()

    def _set_length(self, d):
        """Deja el fragmento en d segundos exactos desde la entrada (o lo corre si no entra)."""
        tl = self.timeline
        if not tl.dur:
            return
        d = min(d, tl.dur)
        start = tl.t_in
        if start + d > tl.dur:
            start = tl.dur - d
        tl.t_in, tl.t_out = start, start + d
        tl.ensure_visible(tl.t_out)
        self._update_marks()

    def _set_in(self, t, commit=False):
        tl = self.timeline
        if not tl.dur:
            return
        tl.t_in = max(0.0, min(t, tl.t_out - 0.05))
        self._update_marks()

    def _set_out(self, t, commit=False):
        tl = self.timeline
        if not tl.dur:
            return
        tl.t_out = min(tl.dur, max(t, tl.t_in + 0.05))
        self._update_marks()

    def _typed_mark(self, setter, var):
        try:
            getattr(self, setter)(parse_time(var.get()), commit=True)
        except ValueError:
            self._update_marks()

    def _seek(self, t, live=False):
        tl = self.timeline
        if not tl.dur:
            return
        tl.ph = min(tl.dur, max(0.0, t))
        tl.ensure_visible(tl.ph)
        self.lbl_time.configure(text=fmt_ms(tl.ph))
        tl.redraw()
        if self.playing:
            if not live:
                self._start_playback(tl.ph)
        else:
            self._schedule_preview(delay=60 if live else 10)

    def _step(self, secs):
        if self.playing:
            self._stop_play()
        self._seek(self.timeline.ph + secs)

    def _step_frames(self, n):
        if self.info:
            self._step(n / (self.info["fps"] or 30))

    # navegación
    def _go_video_start(self):
        self._seek(0.0)

    def _go_video_end(self):
        if self.info:
            self._seek(max(0.0, self.timeline.dur - 1.0 / (self.info["fps"] or 30)))

    def _go_block_start(self):
        self._seek(self.timeline.t_in)

    def _go_block_end(self):
        tl = self.timeline
        if self.info:
            self._seek(max(tl.t_in, tl.t_out - 1.0 / (self.info["fps"] or 30)))

    def _stop_button(self):
        """1er clic: detiene y vuelve al inicio del bloque. Si ya está ahí: al inicio del video."""
        tl = self.timeline
        if not tl.dur:
            return
        if self.playing:
            self._stop_play()
            self._seek(tl.t_in)
        elif abs(tl.ph - tl.t_in) > 1e-3:
            self._seek(tl.t_in)
        else:
            self._seek(0.0)

    def _move_block(self, new_in, length):
        tl = self.timeline
        tl.t_in = new_in
        tl.t_out = new_in + length
        self._update_marks()

    def _tl_press(self, kind):
        self._was_playing = self.playing
        if self.playing and kind == "ph":
            self._stop_play()

    def _tl_release(self, kind):
        if kind == "block":
            if self.playing:
                self._start_playback(self.timeline.t_in)
            else:
                self._seek(self.timeline.t_in)
        elif kind == "ph" and getattr(self, "_was_playing", False):
            self._start_playback(self.timeline.ph)
        elif kind in ("in", "out") and not self.playing:
            self._seek(self.timeline.t_in if kind == "in" else max(self.timeline.t_in, self.timeline.t_out - 0.04))

    # ------------------------------------------------ preview fija
    def _pv_k(self):
        W0, H0 = FORMATS[self.vars["format"].get()]
        pw = even(self.PREVIEW_H * W0 / H0)
        return pw / W0

    def _resize_canvas(self):
        W0, H0 = FORMATS[self.vars["format"].get()]
        k = self._pv_k()
        self.canvas.configure(width=even(W0 * k), height=even(H0 * k))

    def _canvas_msg(self, text):
        self.canvas.delete("all")
        self.canvas.update_idletasks()
        w, h = int(self.canvas["width"]), int(self.canvas["height"])
        self.canvas.create_text(w // 2, h // 2, text=text, fill=self.c["dim"], width=w - 40,
                                font=self.font, justify="center")

    def _on_format(self):
        self._resize_canvas()
        if not self.video:
            self._canvas_msg("Cargá un video o pegá un link de YouTube")
        self._changed()

    def _schedule_preview(self, delay=150):
        if not self.video:
            return
        if self._pv_after:
            self.root.after_cancel(self._pv_after)
        self._pv_after = self.root.after(delay, self._render_preview)

    def _render_preview(self):
        self._pv_after = None
        if not self.video or self.playing:
            return
        s = self._current()
        graph = build_filter(s, self.info["width"], self.info["height"], k=self._pv_k())
        t = min(self.timeline.ph, max(0, self.info["duration"] - 0.05))
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(self.video),
                "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
        self._pv_gen += 1
        gen = self._pv_gen
        threading.Thread(target=self._preview_worker, args=(gen, args), daemon=True).start()

    def _preview_worker(self, gen, args):
        try:
            p = run_quiet(args)
            self.q.put(("preview", gen, p.stdout, p.stderr.decode("utf-8", "replace")))
        except Exception as e:
            self.q.put(("preview", gen, b"", str(e)))

    def _show_preview(self, gen, png, err):
        if gen != self._pv_gen or self.playing:
            return
        if not png:
            self._canvas_msg("Error en la vista previa:\n" + (err.strip()[-400:] or "sin datos"))
            self._draw_overlay()
            return
        self._show_image(tk.PhotoImage(data=base64.b64encode(png)))

    def _show_image(self, img):
        self._photo = img
        items = self.canvas.find_withtag("img")
        if items:
            self.canvas.itemconfigure(items[0], image=img)
        else:
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=img, tags="img")
            self._draw_overlay()
        self.canvas.tag_raise("ov")

    # ------------------------------------------------ edición directa sobre la preview
    def _geom(self):
        return fg_geometry(self._current(), self.info["width"], self.info["height"], k=self._pv_k())

    def _draw_overlay(self):
        cv = self.canvas
        cv.delete("ov")
        if not self.video:
            return
        G = self._geom()
        x0, y0, x1, y1 = G["x"], G["y"], G["x"] + G["gw"], G["y"] + G["gh"]
        col = self.c["accent2"]
        cv.create_rectangle(x0, y0, x1, y1, outline="#000000", width=3, tags="ov")
        cv.create_rectangle(x0, y0, x1, y1, outline=col, width=1, dash=(5, 3), tags="ov")
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="#ffffff", outline=col, tags="ov")
        if self.edit and self.edit.get("mode") == "move":
            W, H = G["W"], G["H"]
            if abs(self.vars["fg_offset_x"].get()) < 1e-6:
                cv.create_line(W / 2, 0, W / 2, H, fill=self.c["in"], dash=(3, 3), tags="ov")
            if abs(self.vars["fg_offset_y"].get()) < 1e-6:
                cv.create_line(0, H / 2, W, H / 2, fill=self.c["in"], dash=(3, 3), tags="ov")

    def _cv_hit(self, x, y):
        if not self.video:
            return None
        G = self._geom()
        x0, y0, x1, y1 = G["x"], G["y"], G["x"] + G["gw"], G["y"] + G["gh"]
        for name, hx, hy in (("nw", x0, y0), ("ne", x1, y0), ("sw", x0, y1), ("se", x1, y1)):
            if abs(x - hx) <= 9 and abs(y - hy) <= 9:
                return name
        if x0 <= x <= x1 and y0 <= y <= y1:
            return "move"
        return None

    def _cv_hover(self, e):
        hit = self._cv_hit(e.x, e.y)
        cur = {"move": "fleur", None: ""}.get(hit, "sizing")
        self.canvas.configure(cursor=cur)

    def _cv_press(self, e):
        hit = self._cv_hit(e.x, e.y)
        if not hit:
            self.edit = None
            return
        G = self._geom()
        self.edit = dict(mode="move" if hit == "move" else "resize", corner=hit, mx=e.x, my=e.y, G=G,
                         ox=self.vars["fg_offset_x"].get(), oy=self.vars["fg_offset_y"].get())

    def _cv_motion(self, e):
        ed = self.edit
        if not ed:
            return
        G = ed["G"]
        W, H = G["W"], G["H"]
        if ed["mode"] == "move":
            ox = ed["ox"] + (e.x - ed["mx"]) / W * 100
            oy = ed["oy"] + (e.y - ed["my"]) / H * 100
            if abs(ox) < 1.2:
                ox = 0.0
            if abs(oy) < 1.2:
                oy = 0.0
            self._set_var("fg_offset_x", round(min(60, max(-60, ox)), 2))
            self._set_var("fg_offset_y", round(min(60, max(-60, oy)), 2))
        else:
            c = ed["corner"]
            x0, y0 = G["x"], G["y"]
            x1, y1 = x0 + G["gw"], y0 + G["gh"]
            fx = x0 if "e" in c else x1          # esquina fija opuesta
            fy = y0 if "s" in c else y1
            rw = abs(e.x - fx) / G["gw"]
            rh = abs(e.y - fy) / G["gh"]
            ratio = max(0.05, (rw + rh) / 2)
            new_fw = G["fw"] * ratio
            width = min(250, max(20, new_fw / W * 100))
            self._set_var("fg_width", round(width, 1))
            s = self._current()
            s["fg_width"] = width
            G2 = fg_geometry(s, self.info["width"], self.info["height"], k=self._pv_k())
            nx = fx if "e" in c else fx - G2["gw"]
            ny = fy if "s" in c else fy - G2["gh"]
            self._set_var("fg_offset_x", round((nx - (W - G2["gw"]) / 2) / W * 100, 2))
            self._set_var("fg_offset_y", round((ny - (H - G2["gh"]) / 2) / H * 100, 2))
        self._draw_overlay()
        if not self.playing:
            self._schedule_preview(delay=120)

    def _cv_release(self, e):
        if self.edit:
            self.edit = None
            self._changed()

    def _cv_center(self, e):
        if self._cv_hit(e.x, e.y):
            self._set_var("fg_offset_x", 0.0)
            self._set_var("fg_offset_y", 0.0)
            self._changed()

    def _cv_wheel(self, direction):
        if not self.video:
            return
        w = self.vars["fg_width"].get()
        self._set_var("fg_width", min(250, max(20, w + 2 * direction)))
        self._changed()

    # ------------------------------------------------ reproducción
    def _toggle_play(self):
        if not self.video:
            return
        if self.playing:
            self._stop_play()
            self._schedule_preview(delay=10)
        else:
            tl = self.timeline
            start = tl.ph
            if start < tl.t_in - 1e-3 or start >= tl.t_out - 0.05:
                start = tl.t_in
            self._start_playback(start)

    def _schedule_restart(self):
        if self._restart_after:
            self.root.after_cancel(self._restart_after)
        self._restart_after = self.root.after(350, lambda: self.playing and self._start_playback(self.timeline.ph))

    def _start_playback(self, start):
        self._restart_after = None
        self._kill_play_procs()
        tl = self.timeline
        if start >= tl.t_out - 0.05:
            start = tl.t_in
        dur = tl.t_out - start
        s = self._current()
        k = self._pv_k()
        G = fg_geometry(s, self.info["width"], self.info["height"], k=k)
        fps = min(self.info["fps"] or 30, PLAY_FPS_MAX)
        size = G["W"] * G["H"] * 3
        graph = build_filter(s, self.info["width"], self.info["height"], k=k, fps=fps, out_fmt="rgb24")
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(self.video),
                "-t", f"{dur:.3f}", "-filter_complex", graph, "-map", "[v]",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.play_gen += 1
        gen = self.play_gen
        frames = queue.Queue(maxsize=10)
        self.play = dict(gen=gen, start=start, dur=dur, fps=fps, frames=frames, next=0, t0=None,
                         header=f"P6 {G['W']} {G['H']} 255\n".encode(), size=size,
                         reader_done=False, audio_ready=False, audio_mode=None)
        self.playing = True
        self.btn_play.configure(text="❚❚  Pausa")
        self._status("Cargando…")
        try:
            self.vproc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, bufsize=size * 2)
        except Exception as e:
            self._stop_play()
            messagebox.showerror(APP_NAME, f"No pude iniciar la reproducción:\n{e}")
            return
        threading.Thread(target=self._reader, args=(gen, self.vproc, size, frames), daemon=True).start()
        threading.Thread(target=self._prepare_audio, args=(gen, start, dur), daemon=True).start()
        self.root.after(5, self._tick)

    def _reader(self, gen, proc, size, frames):
        try:
            while gen == self.play_gen:
                data = proc.stdout.read(size)
                if not data or len(data) < size:
                    break
                while gen == self.play_gen:
                    try:
                        frames.put(data, timeout=0.2)
                        break
                    except queue.Full:
                        pass
        except Exception:
            pass
        if gen == self.play_gen:
            self.play["reader_done"] = True

    def _prepare_audio(self, gen, start, dur):
        src = self._audio_src()
        mode = None
        if src:
            if IS_WIN:
                try:
                    TEMP_DIR.mkdir(parents=True, exist_ok=True)
                    wav = TEMP_DIR / f"play_{gen}.wav"
                    r = run_quiet(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
                                   "-i", str(src), "-t", f"{dur:.3f}", "-vn", "-ac", "2", "-ar", "44100",
                                   "-c:a", "pcm_s16le", str(wav)])
                    if r.returncode == 0 and gen == self.play_gen:
                        self.play["wav"] = wav
                        mode = "winsound"
                except Exception:
                    mode = None
            if mode is None and find_tool("ffplay"):
                mode = "ffplay"
                self.play["audio_args"] = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                                           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        if gen == self.play_gen:
            self.play["audio_mode"] = mode
            self.play["audio_ready"] = True

    def _start_audio(self):
        mode = self.play.get("audio_mode")
        lead = 0.0
        if mode == "winsound":
            import winsound
            try:
                winsound.PlaySound(str(self.play["wav"]), winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.wav_path = self.play["wav"]
            except Exception:
                pass
        elif mode == "ffplay":
            try:
                self.aproc = subprocess.Popen(self.play["audio_args"], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              creationflags=NO_WINDOW)
                lead = 0.15
            except Exception:
                pass
        if mode is None and self._audio_src():
            self._status("Reproduciendo sin audio (no encontré cómo reproducirlo)")
        else:
            self._status("Reproduciendo" if mode else "Reproduciendo (el video no tiene audio)")
        return lead

    def _tick(self):
        if not self.playing:
            return
        P = self.play
        frames = P["frames"]
        now = time.perf_counter()
        if P["t0"] is None:
            ready = frames.qsize() >= 3 or P["reader_done"]
            if ready and P["audio_ready"]:
                lead = self._start_audio()
                P["t0"] = time.perf_counter() + lead
            else:
                self.root.after(5, self._tick)
                return
        el = now - P["t0"]
        if el >= 0:
            target = int(el * P["fps"])
            data = None
            while P["next"] <= target:
                try:
                    data = frames.get_nowait()
                    P["next"] += 1
                except queue.Empty:
                    break
            if data is not None:
                self._show_image(tk.PhotoImage(data=P["header"] + data))
            t = P["start"] + min(el, P["dur"])
            tl = self.timeline
            tl.ph = t
            tl.ensure_visible(t)
            tl.redraw()
            self.lbl_time.configure(text=fmt_ms(t))
            finished = el >= P["dur"] or (P["reader_done"] and frames.empty() and el > P["next"] / P["fps"] + 0.3)
            if finished:
                if self.vars["loop"].get():
                    self._start_playback(tl.t_in)
                    return
                self._stop_play()
                return
        self.root.after(8, self._tick)

    def _kill_play_procs(self):
        self.play_gen += 1
        if self.vproc:
            try:
                self.vproc.kill()
            except Exception:
                pass
            self.vproc = None
        if self.aproc:
            try:
                self.aproc.kill()
            except Exception:
                pass
            self.aproc = None
        if IS_WIN and self.wav_path:
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
        old = self.wav_path
        self.wav_path = None
        if old:
            self.root.after(500, lambda: self._unlink(old))

    @staticmethod
    def _unlink(p):
        try:
            Path(p).unlink()
        except Exception:
            pass

    def _stop_play(self):
        was = self.playing
        self.playing = False
        self._kill_play_procs()
        self.btn_play.configure(text="▶  Play")
        if was:
            self._status("")

    # ------------------------------------------------ forma de onda
    def _request_wave(self):
        if self._wave_after:
            self.root.after_cancel(self._wave_after)
        self._wave_after = self.root.after(250, self._render_wave)

    def _render_wave(self):
        self._wave_after = None
        src = self._audio_src()
        tl = self.timeline
        if not src or not tl.dur:
            tl.wave = tl.wave_key = None
            tl.redraw()
            return
        key = tl.view_key()
        if key == tl.wave_key:
            return
        v0, v1, w, h = key
        self._wave_gen += 1
        gen = self._wave_gen
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{v0:.3f}", "-t", f"{v1 - v0:.3f}",
                "-i", str(src), "-filter_complex",
                f"aformat=channel_layouts=mono,showwavespic=s={w}x{h}:colors=0x6c5bd6:scale=sqrt",
                "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]

        def work():
            try:
                p = run_quiet(args)
                self.q.put(("wave", gen, key, p.stdout))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show_wave(self, gen, key, png):
        if gen != self._wave_gen or not png:
            return
        tl = self.timeline
        tl.wave = tk.PhotoImage(data=base64.b64encode(png))
        tl.wave_key = key
        tl.redraw()

    # ------------------------------------------------ exportación
    def _render_args(self, progress=True):
        if not self.video:
            raise ValueError("Primero cargá un video")
        s = self._current()
        tl = self.timeline
        start, end = tl.t_in, tl.t_out
        full = start <= 0.0005 and end >= self.info["duration"] - 0.0005
        out = self.out_var.get().strip()
        if not out:
            raise ValueError("Elegí un archivo de salida")
        graph = build_filter(s, self.info["width"], self.info["height"])
        a = ["ffmpeg", "-hide_banner", "-y"]
        if start > 0.0005:
            a += ["-ss", f"{start:.3f}"]
        a += ["-i", str(self.video)]
        if self.audio:
            if start > 0.0005:
                a += ["-ss", f"{start:.3f}"]
            a += ["-i", str(self.audio)]
        if not full:
            a += ["-t", f"{end - start:.3f}"]
        a += ["-filter_complex", graph, "-map", "[v]", "-map", "1:a:0" if self.audio else "0:a:0?",
              "-c:v", "libx264", "-crf", str(int(s["crf"])), "-preset", s["preset"], "-profile:v", "high",
              "-c:a", "aac", "-b:a", s["audio_bitrate"], "-shortest"]
        if s["segment"]:
            n = int(s["segment_len"])
            outp = Path(out)
            pattern = str(outp.with_name(outp.stem + "_parte%02d" + (outp.suffix or ".mp4")))
            a += ["-force_key_frames", f"expr:gte(t,n_forced*{n})", "-f", "segment",
                  "-segment_time", str(n), "-reset_timestamps", "1",
                  "-segment_format_options", "movflags=+faststart", "-segment_start_number", "1", pattern]
        else:
            a += ["-movflags", "+faststart", out]
        if progress:
            a[1:1] = ["-progress", "pipe:1", "-nostats"]
        return a, end - start

    def _copy_command(self):
        try:
            args, _ = self._render_args(progress=False)
        except Exception as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(" ".join(ps_quote(x) for x in args))
        self._status("Comando copiado al portapapeles (listo para PowerShell)")

    def _render_toggle(self):
        if self.proc:
            self._cancelled = True
            self.proc.terminate()
        else:
            self._start_render()

    def _start_render(self):
        try:
            args, total = self._render_args()
        except Exception as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        out = Path(self.out_var.get())
        if not self.vars["segment"].get() and out.exists():
            if not messagebox.askyesno(APP_NAME, f"Ya existe:\n{out.name}\n\n¿Reemplazarlo?"):
                return
        self._stop_play()
        out.parent.mkdir(parents=True, exist_ok=True)
        self.last_output = out
        self._cancelled = False
        self.btn_folder.pack_forget()
        self.btn_render.configure(text="Cancelar")
        self.progress["value"] = 0
        self._status(f"Exportando {fmt_ms(self.timeline.t_in)} → {fmt_ms(self.timeline.t_out)}…")
        threading.Thread(target=self._render_worker, args=(args, max(total, 0.1)), daemon=True).start()

    def _render_worker(self, args, total):
        err = deque(maxlen=25)
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                         text=True, encoding="utf-8", errors="replace")
        except Exception as e:
            self.q.put(("render_done", False, str(e)))
            return
        th = threading.Thread(target=lambda: [err.append(l.rstrip()) for l in self.proc.stderr], daemon=True)
        th.start()
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith(("out_time_us=", "out_time_ms=")):
                try:
                    self.q.put(("progress", min(100.0, int(line.split("=", 1)[1]) / 1e6 / total * 100)))
                except ValueError:
                    pass
        code = self.proc.wait()
        th.join(timeout=2)
        if self._cancelled:
            self.q.put(("render_done", None, ""))
        else:
            self.q.put(("render_done", code == 0, "\n".join(err)))

    def _render_done(self, ok, err):
        self.proc = None
        self.btn_render.configure(text="Exportar")
        seg = self.vars["segment"].get()
        if ok is None:
            self.progress["value"] = 0
            self._status("Cancelado")
            if self.last_output and not seg:
                self._unlink(self.last_output)
        elif ok:
            self.progress["value"] = 100
            self._status(f"Listo: {self.last_output.name}" + ("  (en partes)" if seg else ""))
            self.btn_folder.pack(side="right", padx=(6, 0))
        else:
            self.progress["value"] = 0
            self._status("Error al exportar")
            messagebox.showerror(APP_NAME, "FFmpeg falló:\n\n" + (err[-1500:] or "sin detalle"))

    def _open_folder(self):
        if not self.last_output:
            return
        if IS_WIN:
            if self.last_output.is_file():
                subprocess.Popen(["explorer", "/select,", str(self.last_output)])
            else:
                os.startfile(str(self.last_output.parent))
        else:
            subprocess.Popen(["xdg-open", str(self.last_output.parent)])

    # ------------------------------------------------ YouTube
    def _download(self):
        if self.dl_proc:
            self.dl_proc.terminate()
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo(APP_NAME, "Pegá el link del video de YouTube")
            return
        base = ["yt-dlp"] if find_tool("yt-dlp") else [sys.executable.replace("pythonw", "python"), "-m", "yt_dlp"]
        args = base + ["-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
                       "-P", str(DOWNLOADS), "-o", "%(title)s.%(ext)s", "--no-playlist",
                       "--newline", "--progress", "--no-update", "--print", "after_move:filepath", url]
        self.btn_dl.configure(text="Cancelar")
        self.progress["value"] = 0
        self._status("Descargando…")
        threading.Thread(target=self._download_worker, args=(args,), daemon=True).start()

    def _download_worker(self, args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        tail = deque(maxlen=15)
        path = None
        try:
            self.dl_proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env,
                                            text=True, encoding="utf-8", errors="replace")
        except Exception as e:
            self.q.put(("dl_done", None, f"No pude ejecutar yt-dlp: {e}"))
            return
        for line in self.dl_proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m:
                self.q.put(("progress", float(m.group(1))))
                continue
            if os.path.isfile(line.strip()):
                path = line.strip()
            tail.append(line)
            if "[Merger]" in line:
                self.q.put(("status", "Uniendo video y audio…"))
        code = self.dl_proc.wait()
        self.q.put(("dl_done", path if code == 0 else None, "\n".join(tail)))

    def _download_done(self, path, log):
        self.dl_proc = None
        self.btn_dl.configure(text="Bajar 1080p")
        if path:
            self.progress["value"] = 100
            self._status("Descarga lista")
            self.url_var.set("")
            self._load_video(Path(path))
        else:
            self.progress["value"] = 0
            self._status("La descarga no terminó")
            if log and "Terminated" not in log:
                messagebox.showerror(APP_NAME, "yt-dlp falló:\n\n" + log[-1500:] +
                                     "\n\nSi es un error de formato, actualizá yt-dlp:\npython -m pip install -U yt-dlp")

    # ------------------------------------------------ loop
    def _status(self, text):
        self.lbl_status.configure(text=text)

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "preview":
                    self._show_preview(*msg[1:])
                elif kind == "wave":
                    self._show_wave(*msg[1:])
                elif kind == "progress":
                    self.progress["value"] = msg[1]
                elif kind == "status":
                    self._status(msg[1])
                elif kind == "render_done":
                    self._render_done(msg[1], msg[2])
                elif kind == "dl_done":
                    self._download_done(msg[1], msg[2])
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _on_close(self):
        if self.proc and not messagebox.askyesno(APP_NAME, "Hay una exportación en curso. ¿Cancelar y salir?"):
            return
        self._stop_play()
        for p in (self.proc, self.dl_proc):
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass
        self._save_settings()
        try:
            for f in TEMP_DIR.glob("play_*.wav"):
                self._unlink(f)
        except Exception:
            pass
        self.root.destroy()


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.minsize(1100, 800)
    app = StoryMaker(root)
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        app._load_video(Path(sys.argv[1]))
    root.mainloop()


if __name__ == "__main__":
    main()
