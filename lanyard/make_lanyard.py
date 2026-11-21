#!/usr/bin/env python3
"""Falera Badge lanyard: print artwork and a preview.

The strap is 12 mm wide (the badge's lanyard slots are 13.6 x 3.3 mm, so a
12 mm strap and its fold fit through them). It is printed on one side, 950 mm
long: 900 mm shows, and 25 mm at each end folds through a slot and is sewn.

The pattern repeats along the strap and turns 180 degrees at the middle, so
when the lanyard is worn both sides read the same way, from the badge up.

Writes:
    lanyard-print.svg          the artwork alone, 950 x 12 mm (for the printer)
    lanyard-guides.svg         the artwork with the fold, sew and centre lines
    renders/lanyard-print.pdf  the artwork as a PDF at 950 x 12 mm
    renders/detail.png         the first 330 mm, enlarged, in three rows
    renders/preview.png        the lanyard on the badge

Run: python3 make_lanyard.py   (needs a headless Chromium, see
name-card/make_cards.py, and pdftoppm for the badge picture in the preview)
"""

import base64
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "name-card"))
from make_cards import chrome  # noqa: E402

# Strap (mm)
L, WID = 950.0, 12.0
END = 25.0                      # folded through the slot and sewn, each end
SEW = 12.0                      # sew line, this far from each end
MID = L / 2
SAFE = 1.2                      # keep artwork this far from the long edges

INK, MUTED, RED, RED_DARK = "#1f2328", "#59636e", "#c93a45", "#a92f39"
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace'

BOW = ('<path d="M18 12C12 3 3 1 2 7c-1 5-1 10 1 13 3 3 10-1 15-8z" fill="{r}"/>'
       '<path d="M22 12c6-9 15-11 16-5 1 5 1 10-1 13-3 3-10-1-15-8z" fill="{r}"/>'
       '<rect x="15.5" y="7" width="9" height="10" rx="3.5" fill="{d}"/>').format(r=RED, d=RED_DARK)


def lumi_href():
    # Lumi facing the viewer (circuit/elixpo/art/lumi-front-lineart.png, lines
    # thickened, in the card's ink), turned so she stands upright when worn.
    data = (HERE / "assets" / "lumi-front.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def brand_wave(n):
    """n values in 0..1: SHA-256 of "falera:<k>" chained, smoothed like the card's."""
    raw = []
    k = 0
    while len(raw) < n + 2:
        raw += list(hashlib.sha256(f"falera:{k}".encode()).digest())
        k += 1
    raw = [v / 255 for v in raw]
    return [(raw[i] + 2 * raw[i + 1] + raw[i + 2]) / 4 for i in range(n)]


class Wave:
    """Hands out consecutive slices of one long sequence, so the strap's
    waveform pieces read as one band broken by the text."""
    PITCH, BAR, H = 0.9, 0.5, 7.4

    def __init__(self):
        self.vals = brand_wave(4000)
        self.i = 0

    def draw(self, x, length):
        n = int(length / self.PITCH)
        vals = self.vals[self.i:self.i + n]
        self.i += n
        peak = max(range(n), key=lambda k: vals[k])
        out = []
        for k, v in enumerate(vals):
            h = max(0.6, (0.18 + 0.82 * v) * self.H)
            col = RED if abs(k - peak) <= 1 else INK
            out.append(f'<rect x="{x + k * self.PITCH:.2f}" y="{WID / 2 - h / 2:.2f}" width="{self.BAR}" '
                       f'height="{h:.2f}" rx="{self.BAR / 2}" fill="{col}"/>')
        return "".join(out)


def text(x, s, size, weight, fill, length, spacing=True):
    # textLength fixes the width, so the layout doesn't depend on the font.
    base = WID / 2 + size * 0.36
    adj = "spacing" if spacing else "spacingAndGlyphs"
    return (f'<text x="{x:.2f}" y="{base:.2f}" font-family=\'{MONO}\' font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" textLength="{length}" lengthAdjust="{adj}">{s}</text>')


def module(x, wave, lumi):
    """One 150 mm repeat, starting at x. Widths in mm."""
    parts = []
    parts.append(f'<g transform="translate({x:.2f} {WID / 2 - 3.6:.2f}) scale(0.3)">{BOW}</g>')   # bow, 12 x 7.2
    x += 12 + 4
    parts.append(text(x, "FALERA BADGE", 3.9, 800, INK, 34))
    x += 34 + 5
    parts.append(wave.draw(x, 22))
    x += 22 + 5
    parts.append(f'<image href="{lumi}" x="{x + 0.6:.2f}" y="{WID / 2 - 4.7:.2f}" width="8.3" height="9.4"/>')   # 1443 x 1635 px
    x += 9.5 + 4
    parts.append(text(x, "falera.elixpo.com", 3.1, 600, MUTED, 27))
    x += 27 + 3
    parts.append(text(x, "by Elixpo", 2.6, 700, RED, 13))
    return "".join(parts)


MODULE_W = 12 + 4 + 34 + 5 + 22 + 5 + 9.5 + 4 + 27 + 3 + 13     # 138.5 mm
MIN_GAP = 5.0
CENTRE = 24.0                   # kept clear at the neck for the centre bow alone


def half(lumi, wave):
    """The artwork from the sewn end to the middle, in strap coordinates."""
    parts = []
    usable = MID - END - CENTRE / 2                   # stop short of the centre bow
    count = int(usable // (MODULE_W + MIN_GAP))         # 3 per half
    gap = (usable - count * MODULE_W) / count        # spread the spare length evenly
    x = END + gap / 2
    for _ in range(count):
        parts.append(module(x, wave, lumi))
        x += MODULE_W + gap
    return "".join(parts)


def artwork(guides=False):
    lumi = lumi_href()
    first = half(lumi, Wave())
    # The second half is the first turned 180 degrees about the strap's centre.
    body = [f'<rect width="{L}" height="{WID}" fill="#fff"/>']
    for y in (SAFE, WID - SAFE):
        body.append(f'<line x1="0" y1="{y}" x2="{L}" y2="{y}" stroke="{RED}" stroke-width="0.35"/>')
    body.append(f'<rect x="0" y="0" width="{END}" height="{WID}" fill="{RED}"/>')
    body.append(f'<rect x="{L - END}" y="0" width="{END}" height="{WID}" fill="{RED}"/>')
    body.append(first)
    body.append(f'<g transform="rotate(180 {MID} {WID / 2})">{first}</g>')
    # At the neck: one bow on the centre line.
    body.append(f'<g transform="translate({MID - 6} {WID / 2 - 3.6}) scale(0.3)">{BOW}</g>')
    if guides:
        g = 'stroke="#0a7cff" stroke-width="0.2" fill="none"'
        for x in (END, L - END):
            body.append(f'<line x1="{x}" y1="-3" x2="{x}" y2="{WID + 3}" {g} stroke-dasharray="1 0.6"/>')
        for x in (SEW, L - SEW):
            body.append(f'<line x1="{x}" y1="-3" x2="{x}" y2="{WID + 3}" stroke="#16a34a" stroke-width="0.25" stroke-dasharray="0.4 0.4"/>')
        body.append(f'<line x1="{MID}" y1="-4" x2="{MID}" y2="{WID + 4}" stroke="#0a7cff" stroke-width="0.2"/>')
        lab = 'font-family="sans-serif" font-size="2.4" fill="#0a7cff"'
        body.append(f'<text x="{END + 1}" y="-1" {lab}>fold through the slot ({END:g} mm)</text>')
        body.append(f'<text x="{SEW}" y="{WID + 3.2}" {lab} fill="#16a34a">sew {SEW:g} mm from the end</text>')
        body.append(f'<text x="{MID + 1}" y="-1" {lab}>centre (neck): pattern turns 180°</text>')
        body.append(f'<text x="0" y="{WID + 7}" {lab}>{L:g} × {WID:g} mm, one side printed; artwork {SAFE} mm clear of the long edges</text>')
        vb = f"0 -6 {L} {WID + 15}"
        size = f'width="{L}mm" height="{WID + 15}mm"'
    else:
        vb = f"0 0 {L} {WID}"
        size = f'width="{L}mm" height="{WID}mm"'
    return f'<svg xmlns="http://www.w3.org/2000/svg" {size} viewBox="{vb}">{"".join(body)}</svg>'


def run(exe, args):
    subprocess.run([exe, "--headless", "--no-sandbox", "--hide-scrollbars", "--allow-file-access-from-files"] + args,
                   capture_output=True, check=True)


def main():
    exe = chrome()
    if not exe:
        sys.exit("no headless Chromium found: set CHROME to its path")
    art = artwork()
    (HERE / "lanyard-print.svg").write_text(art)
    (HERE / "lanyard-guides.svg").write_text(artwork(guides=True))
    out = HERE / "renders"
    out.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # PDF at the strap's real size.
        page = tmp / "print.html"
        page.write_text(f'<!doctype html><style>@page {{ size: {L}mm {WID}mm; margin: 0 }} body {{ margin: 0 }} '
                        f'svg {{ display: block }}</style>{art}')
        run(exe, ["--no-pdf-header-footer", f"--print-to-pdf={out / 'lanyard-print.pdf'}", page.as_uri()])

        # Detail: the first 330 mm in three rows of 110 mm, at 10 px/mm.
        rows = "".join(
            f'<div class="row"><svg viewBox="{k * 110} 0 110 {WID}" width="1100" height="120">{art[art.index(">") + 1:-6]}</svg>'
            f'<span>{k * 110}–{(k + 1) * 110} mm</span></div>' for k in range(3))
        det = tmp / "detail.html"
        det.write_text('<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#eaeef2;font:13px sans-serif;color:#59636e;padding:20px}'
                       '.row{margin-bottom:22px}.row svg{display:block;background:#fff;outline:1px solid #d1d9e0}'
                       f'.row span{{display:block;margin-top:5px}}</style>{rows}')
        run(exe, ["--window-size=1140,520", f"--screenshot={out / 'detail.png'}", det.as_uri()])

        # Preview: the badge with the strap rising from both slots.
        card_pdf = tmp / "card.pdf"
        run(exe, ["--no-pdf-header-footer", f"--print-to-pdf={card_pdf}",
                  (ROOT / "name-card" / "cards" / "Circuit-Overtime.html").as_uri() + "?card"])
        subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile", str(card_pdf), str(tmp / "card")], check=True)
        strap_png = tmp / "strap.png"
        strap = tmp / "strap.html"
        strap.write_text(f'<!doctype html><style>body{{margin:0}} svg{{display:block}}</style>'
                         f'<svg viewBox="0 0 {L} {WID}" width="{int(L * 4)}" height="{int(WID * 4)}">{art[art.index(">") + 1:-6]}</svg>')
        run(exe, [f"--window-size={int(L * 4)},{int(WID * 4)}", f"--screenshot={strap_png}", strap.as_uri()])
        prev = tmp / "preview.html"
        prev.write_text(preview_html((tmp / "card.png").as_uri(), strap_png.as_uri()))
        run(exe, ["--window-size=900,1200", f"--screenshot={out / 'preview.png'}", prev.as_uri()])
    for f in ("lanyard-print.svg", "lanyard-guides.svg", "renders/lanyard-print.pdf", "renders/detail.png", "renders/preview.png"):
        print(f)


def preview_html(card, strap):
    """The badge (black board, the card, the screen) with the strap from each
    slot, at 4 px/mm. The strap image starts at the sewn end, so it runs from
    the slot upwards; both sides read the same way, as worn."""
    s = 4                                     # px per mm
    bx, by = 450 - 44 * s, 600                # board top-left
    slots = (10.0, 78.0)                      # slot centres, card x (mm)
    tilt = 11                                 # degrees from vertical
    straps = []
    for cx, sign in zip(slots, (-1, 1)):
        x = bx + cx * s
        y = by + 5.5 * s
        # The image is drawn along +x from the sewn end; rotate so it rises from the slot.
        straps.append(f'<img class="strap" src="{strap}" style="left:{x}px;top:{y}px;'
                      f'transform: rotate({-90 + sign * tilt}deg) translateX({-END * s}px) translateY(-{WID * s / 2}px)">')
    return f"""<!doctype html><meta charset="utf-8"><style>
body {{ margin: 0; width: 900px; height: 1200px; background: #eaeef2; position: relative; overflow: hidden; font: 13px sans-serif; }}
.strap {{ position: absolute; width: {L * s}px; height: {WID * s}px; transform-origin: 0 0; }}
.board {{ position: absolute; left: {bx}px; top: {by}px; width: {88 * s}px; height: {133.6 * s}px; background: #111316; border-radius: {3 * s}px {3 * s}px {2 * s}px {2 * s}px; }}
.card {{ position: absolute; left: {bx}px; top: {by}px; width: {88 * s}px; height: {59.5 * s}px; }}
.lcd {{ position: absolute; left: {bx + 1 * s}px; top: {by + 60 * s}px; width: {86 * s}px; height: {50 * s}px; background: #b8322f; border-radius: 3px; }}
.lcd i {{ position: absolute; left: {6 * s}px; top: {3.5 * s}px; right: {6 * s}px; bottom: {3.5 * s}px; background: #0d0f12; }}
.cap {{ position: absolute; left: 0; right: 0; bottom: 12px; text-align: center; color: #59636e; }}
</style>
{"".join(straps)}
<div class="board"></div><img class="card" src="{card}"><div class="lcd"><i></i></div>
<p class="cap">Preview at 4 px/mm: 12 mm strap through both slots, ends folded and sewn.</p>"""


if __name__ == "__main__":
    main()
