#!/usr/bin/env python3
"""Build one HTML name card per person in people.json, into cards/.

Each card is drawn at its real size (88 x 59.5 mm) with card.css, clipped to
the badge's shape (lanyard slots and the IR notch, from
template/name-card-mask.svg, which make_name_card.py writes).

Open a card in a browser to preview it; print it at 100% on A4 for a
real-size card with a cut line and crop marks.

Run: python3 make_cards.py   (needs the qrcode package: pip install qrcode)
"""

import hashlib
import html
import json
import re
from pathlib import Path

import qrcode
import qrcode.image.svg

HERE = Path(__file__).resolve().parent
W, H = 88.0, 59.5

GITHUB_MARK = ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
               '5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82'
               '-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07'
               '-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 '
               '1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 '
               '2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 '
               '8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>')


def card_path():
    svg = (HERE / "template" / "name-card-mask.svg").read_text()
    return re.search(r' d="([^"]+)"', svg).group(1)


def digest(handle):
    return hashlib.sha256(handle.lower().encode()).digest()


def wave(handle, n=64, w=64.0, h=12.0):
    """The handle's SHA-256 drawn as a mirrored waveform: one bar per byte
    pair, smoothed so it reads like sound. Unique per handle."""
    d = digest(handle) + digest(handle + "#")       # 64 bytes
    raw = [d[k] / 255 for k in range(n)]
    sm = [(raw[k - 1] + 2 * raw[k] + raw[(k + 1) % n]) / 4 for k in range(n)]
    step = w / n
    bw = step * 0.55
    mid = h / 2
    bars = []
    peak = max(range(n), key=lambda k: sm[k])
    for k, v in enumerate(sm):
        bh = max(0.5, (0.15 + 0.85 * v) * h)
        x = k * step + (step - bw) / 2
        cls = "wb hot" if abs(k - peak) <= 1 else "wb"
        bars.append(f'<rect class="{cls}" x="{x:.3f}" y="{mid - bh / 2:.3f}" width="{bw:.3f}" height="{bh:.3f}" rx="{bw / 2:.3f}"/>')
    return (f'<svg class="wave" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<line class="wmid" x1="0" y1="{mid}" x2="{w}" y2="{mid}"/>' + "".join(bars) + "</svg>")


def ring(handle, r_out=13.0):
    """A glyph border for the photo: a dial of ticks with a few long ones and
    one red arc, all placed from the handle's hash."""
    d = digest(handle)
    c = r_out
    size = 2 * r_out
    parts = []
    ticks = 72
    for k in range(ticks):
        a = 2 * 3.14159265 * k / ticks - 3.14159265 / 2
        long_ = d[k % 32] % 7 == 0
        r1 = r_out - (2.1 if long_ else 1.1)
        r2 = r_out - 0.3
        import math
        x1, y1 = c + r1 * math.cos(a), c + r1 * math.sin(a)
        x2, y2 = c + r2 * math.cos(a), c + r2 * math.sin(a)
        parts.append(f'<line class="{"tk long" if long_ else "tk"}" x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}"/>')
    import math
    start = d[0] / 255 * 360
    sweep = 50 + d[1] / 255 * 70
    ra = r_out - 2.8
    a0, a1 = math.radians(start - 90), math.radians(start + sweep - 90)
    large = 1 if sweep > 180 else 0
    parts.append(f'<path class="arc" d="M{c + ra * math.cos(a0):.3f},{c + ra * math.sin(a0):.3f} '
                 f'A{ra},{ra} 0 {large} 1 {c + ra * math.cos(a1):.3f},{c + ra * math.sin(a1):.3f}"/>')
    # A small dot at the arc's end, like a dial pointer.
    parts.append(f'<circle class="dot" cx="{c + ra * math.cos(a1):.3f}" cy="{c + ra * math.sin(a1):.3f}" r="0.55"/>')
    return f'<svg class="ring" viewBox="0 0 {size} {size}" aria-hidden="true">{"".join(parts)}</svg>'


def background(handle, w=88.0, h=59.5):
    """Faint abstract geometry behind the card, laid out from the hash."""
    import math
    d = digest(handle + "bg")
    shapes = []
    # A fine dot grid on the right half.
    for gx in range(46, 88, 3):
        for gy in range(30, 60, 3):
            shapes.append(f'<circle class="bgd" cx="{gx}" cy="{gy}" r="0.18"/>')
    # Three large circles and two triangles.
    for k in range(3):
        cx, cy = 10 + d[k] / 255 * 70, 8 + d[k + 3] / 255 * 45
        r = 8 + d[k + 6] / 255 * 14
        shapes.append(f'<circle class="bgs" cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"/>')
    for k in range(2):
        cx, cy = 12 + d[k + 9] / 255 * 64, 10 + d[k + 11] / 255 * 40
        s = 7 + d[k + 13] / 255 * 9
        rot = d[k + 15] / 255 * 120
        pts = " ".join(f"{cx + s * math.cos(math.radians(rot + 120 * t)):.2f},{cy + s * math.sin(math.radians(rot + 120 * t)):.2f}" for t in range(3))
        shapes.append(f'<polygon class="bgs" points="{pts}"/>')
    # Two long diagonal hairlines.
    for k in range(2):
        y0 = d[17 + k] / 255 * h
        shapes.append(f'<line class="bgs" x1="0" y1="{y0:.2f}" x2="{w}" y2="{(y0 + 22 + d[19 + k] / 255 * 20) % h:.2f}"/>')
    return f'<svg class="bg" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">{"".join(shapes)}</svg>'


def qr_svg(url):
    """A custom QR: round dots, square finders with red centres (rounded finders
    stopped OpenCV decoding it, so they stay square), the GitHub mark in the
    middle (error correction Q, 25%, covers the mark)."""
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, border=0)
    q.add_data(url)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    hole = n // 2 - 3, n // 2 + 3          # leave the centre for the mark
    finders = [(0, 0), (n - 7, 0), (0, n - 7)]

    def in_finder(x, y):
        return any(fx <= x < fx + 7 and fy <= y < fy + 7 for fx, fy in finders)

    dots = []
    for y in range(n):
        for x in range(n):
            if not m[y][x] or in_finder(x, y):
                continue
            if hole[0] <= x <= hole[1] and hole[0] <= y <= hole[1]:
                continue
            dots.append(f'<circle cx="{x + 0.5}" cy="{y + 0.5}" r="0.42"/>')
    fs = []
    for fx, fy in finders:
        fs.append(f'<rect class="fo" x="{fx + 0.5}" y="{fy + 0.5}" width="6" height="6"/>'
                  f'<rect class="fi" x="{fx + 2}" y="{fy + 2}" width="3" height="3"/>')
    c = n / 2
    mark = (f'<g transform="translate({c - 2.6} {c - 2.6}) scale({5.2 / 16})">'
            + GITHUB_MARK.split('aria-hidden="true">')[1].replace("</svg>", "") + "</g>")
    return (f'<svg class="qr" viewBox="0 0 {n} {n}" aria-hidden="true"><g class="qd">{"".join(dots)}</g>'
            f'{"".join(fs)}{mark}</svg>')


def konami():
    """The Konami code's arrows as tiny SVG chevrons (font arrows render unevenly)."""
    rot = {"u": 0, "d": 180, "l": 270, "r": 90}
    out = []
    for k in "uuddlrlr":
        out.append(f'<svg viewBox="0 0 10 10"><path d="M5 1.5 L8.5 7.5 H1.5 Z" transform="rotate({rot[k]} 5 5)"/></svg>')
    return "".join(out)


def page(p, path):
    e = html.escape
    url = f"https://github.com/{p['handle']}"
    marks = []
    # Crop marks at the card's four corners, printed outside the card (print only).
    for x, y in ((0, 0), (W, 0), (0, H), (W, H)):
        sx = -1 if x == 0 else 1
        sy = -1 if y == 0 else 1
        marks.append(f'<span class="crop" style="left:{30 + x + (2 if sx > 0 else -6)}mm;top:{30 + y - 0.05}mm;width:4mm;height:0.1mm"></span>')
        marks.append(f'<span class="crop" style="left:{30 + x - 0.05}mm;top:{30 + y + (2 if sy > 0 else -6)}mm;width:0.1mm;height:4mm"></span>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Name card · {e(p['name'])}</title>
<link rel="stylesheet" href="../card.css">
</head>
<body>
<!-- Card shape in mm (from template/name-card-mask.svg), used as a clip path. -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <clipPath id="card-shape" clipPathUnits="objectBoundingBox" transform="scale({1 / W:.8f} {1 / H:.8f})">
    <path clip-rule="evenodd" d="{path}"/>
  </clipPath>
</svg>

<div class="stage">
  <div class="card">
    {background(p['handle'])}
    <div class="top-label">FALERA BADGE</div>
    {ring(p['handle'])}
    <img class="avatar" src="../{e(p['photo'])}" alt="{e(p['name'])}">
    <div class="who">
      <div class="name">{e(p['name'])}</div>
      <div class="handle">{GITHUB_MARK}@{e(p['handle'])}</div>
      <div class="role">{e(p['role'])} <b>@{e(p['org'])}</b></div>
    </div>
    {wave(p['handle'])}
    <div class="wave-cap"><span>sha256(@{e(p['handle'])}) · {digest(p['handle']).hex()[:8]}</span><span>No. {p['no']:02d}</span></div>
    {qr_svg(url)}
    <div class="qr-cap">github.com/{e(p['handle'])}</div>
    <img class="lumi" src="../assets/lumi-line.png" alt="">
    <div class="foot"><img src="../assets/bow.svg" alt="">falera.elixpo.com</div>
    <div class="konami" title="Enter this on the badge to hear the waveform">{konami()}<b>B</b><b>A</b><span>♪</span></div>
    <svg class="cutline" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true"><path fill-rule="evenodd" d="{path}"/></svg>
  </div>
</div>
{"".join(marks)}
<p class="hint">Preview at 2.8×. Print on A4 at 100% (no scaling) for a real-size card: cut along the grey line.
Add <code>?cut</code> to the URL to see the cut line here.</p>
<script>if (location.search.includes("cut")) document.body.classList.add("show-cut");</script>
</body>
</html>
"""


if __name__ == "__main__":
    path = card_path()
    out = HERE / "cards"
    out.mkdir(exist_ok=True)
    for i, p in enumerate(json.loads((HERE / "people.json").read_text()), 1):
        p.setdefault("no", i)
        f = out / f"{p['handle']}.html"
        f.write_text(page(p, path))
        print(f)
