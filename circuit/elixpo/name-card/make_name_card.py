#!/usr/bin/env python3
"""Draw the base plane for the Falera Badge's front name card.

The name card is the printed photo-paper card on the front of the badge, in
the band between the board's top edge and the top of the LCD module. It wraps
around the IR receiver (U8) and the IR LED (D10) on the right edge, and stays
clear of the two lanyard slots.

Every number comes from circuit/elixpo/elixpo.kicad_pcb (KiCad 10, read with
pcbnew on 2026-11-22). Board coordinates are in mm, y pointing down; the
card's origin (0, 0) is the board's top-left corner (board x 40, y 30).

Writes, next to this script:
  name-card.svg         1:1 in millimetres: cut line, cut-outs, guides
  name-card-print.svg   the cut line and cut-outs only, for a print template

Run: python3 make_name_card.py
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── Board, in board coordinates (mm) ─────────────────────────────────────
BOARD_X0, BOARD_Y0 = 40.0, 30.0     # top-left of Edge.Cuts
BOARD_W = 88.0                      # 40 → 128
CORNER_R = 3.0                      # board corner radius
LCD_TOP = 90.0                      # F.Fab outline of the 2.8" LCD module (41–127 × 90–140)

# Lanyard slots (Edge.Cuts stadiums): x span, y span, end radius.
SLOTS = [((43.2, 56.8), (33.85, 37.15)), ((111.2, 124.8), (33.85, 37.15))]

# Parts the card must wrap around (front courtyards).
U8_IR_RECEIVER = ((122.25, 127.69), (50.30, 57.77))   # TSOP38238, MINICAST body
D10_IR_LED = ((115.17, 119.83), (53.83, 56.17))       # 940 nm, 1206

# Guides only (the card may cover them; marked so the design can avoid them).
SPEAKER_GRILLE = ((111.40, 124.90), (39.10, 49.90))   # G1, holes through to LS1
CORNER_LEDS = [((42.83, 44.38), (39.48, 42.52)), ((125.62, 127.17), (37.48, 40.52))]  # D1, D2

# ── Card ─────────────────────────────────────────────────────────────────
CLEAR = 0.5                 # clearance around every cut-out
CARD_H = LCD_TOP - BOARD_Y0 - CLEAR   # stops 0.5 mm above the LCD module
INNER_R = 1.0               # radius on the IR notch's inside corners


def c(x, y):
    """Board → card coordinates."""
    return round(x - BOARD_X0, 3), round(y - BOARD_Y0, 3)


def stadium(x0, x1, y0, y1):
    r = (y1 - y0) / 2
    return (f"M{x0 + r:.3f},{y0:.3f} H{x1 - r:.3f} A{r:.3f},{r:.3f} 0 0 1 {x1 - r:.3f},{y1:.3f} "
            f"H{x0 + r:.3f} A{r:.3f},{r:.3f} 0 0 1 {x0 + r:.3f},{y0:.3f} Z")


def card_outline():
    """The card's outer edge, with the IR notch cut from its right side.

    The notch covers U8 and D10 together: they sit 1.4 mm apart, too close
    for a strip of paper between them to survive."""
    w, h, r = BOARD_W, CARD_H, CORNER_R
    nx0 = c(D10_IR_LED[0][0], 0)[0] - CLEAR                  # notch left edge
    ny0 = c(0, min(U8_IR_RECEIVER[1][0], D10_IR_LED[1][0]))[1] - CLEAR
    ny1 = c(0, max(U8_IR_RECEIVER[1][1], D10_IR_LED[1][1]))[1] + CLEAR
    ir = INNER_R
    return (
        f"M{r},0 H{w - r} A{r},{r} 0 0 1 {w},{r} "
        f"V{ny0:.3f} H{nx0 + ir:.3f} A{ir},{ir} 0 0 0 {nx0:.3f},{ny0 + ir:.3f} "
        f"V{ny1 - ir:.3f} A{ir},{ir} 0 0 0 {nx0 + ir:.3f},{ny1:.3f} H{w} "
        f"V{h} H0 V{r} A{r},{r} 0 0 1 {r},0 Z"
    ), (nx0, ny0, ny1)


def slot_paths():
    out = []
    for (xa, xb), (ya, yb) in SLOTS:
        x0, y0 = c(xa, ya)
        x1, y1 = c(xb, yb)
        out.append(stadium(x0 - CLEAR, x1 + CLEAR, y0 - CLEAR, y1 + CLEAR))
    return out


def rect(box, cls, label=None):
    (xa, xb), (ya, yb) = box
    x0, y0 = c(xa, ya)
    x1, y1 = c(xb, yb)
    s = f'<rect class="{cls}" x="{x0}" y="{y0}" width="{x1 - x0:.3f}" height="{y1 - y0:.3f}" rx="0.4"/>'
    if label:
        s += f'<text class="lab" x="{(x0 + x1) / 2:.2f}" y="{(y0 + y1) / 2:.2f}">{label}</text>'
    return s


def build(guides=True):
    outline, (nx0, ny0, ny1) = card_outline()
    w, h = BOARD_W, CARD_H
    style = """
    .cut   { fill: #ffffff; stroke: #c93a45; stroke-width: 0.25; }
    .hole  { fill: #ffffff; stroke: #c93a45; stroke-width: 0.25; }
    .guide { fill: none; stroke: #1d2420; stroke-width: 0.15; stroke-dasharray: 0.8 0.6; opacity: 0.55; }
    .safe  { fill: none; stroke: #2e7d5b; stroke-width: 0.15; stroke-dasharray: 0.4 0.4; }
    .lab   { font: 1.6px Arial, sans-serif; fill: #1d2420; opacity: 0.7; text-anchor: middle; dominant-baseline: middle; }
    .dim   { font: 1.8px Arial, sans-serif; fill: #c93a45; text-anchor: middle; }
    """
    body = [f'<path class="cut" fill-rule="evenodd" d="{outline} {" ".join(slot_paths())}"/>']
    if guides:
        # 2 mm safe area inside the card and around the cut-outs.
        body.append(f'<rect class="safe" x="2" y="{c(0, SLOTS[0][1][1])[1] + CLEAR + 2:.2f}" '
                    f'width="{c(SPEAKER_GRILLE[0][0], 0)[0] - CLEAR - 4:.2f}" height="{h - (c(0, SLOTS[0][1][1])[1] + CLEAR + 2) - 2:.2f}"/>')
        body.append(rect(U8_IR_RECEIVER, "guide", "IR rx"))
        body.append(rect(D10_IR_LED, "guide", "IR tx"))
        body.append(rect(SPEAKER_GRILLE, "guide", "speaker grille"))
        for i, led in enumerate(CORNER_LEDS, 1):
            body.append(rect(led, "guide"))
        # The LCD's top edge and the board outline below the card.
        body.append(f'<line class="guide" x1="0" y1="{LCD_TOP - BOARD_Y0}" x2="{w}" y2="{LCD_TOP - BOARD_Y0}"/>')
        body.append(f'<text class="lab" x="{w / 2}" y="{LCD_TOP - BOARD_Y0 + 1.6}">top of the LCD module</text>')
        body.append(f'<text class="dim" x="{w / 2}" y="-2">88.0 mm</text>')
        body.append(f'<text class="dim" x="-2" y="{h / 2}" transform="rotate(-90 -2 {h / 2})">{h:.1f} mm</text>')
    pad = 5 if guides else 0
    vb = f"{-pad} {-pad} {w + 2 * pad} {h + 2 * pad + (3 if guides else 0)}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w + 2 * pad}mm" '
            f'height="{h + 2 * pad + (3 if guides else 0)}mm" viewBox="{vb}">\n'
            f'<title>Falera Badge name card, 1:1 (mm)</title>\n<style>{style}</style>\n'
            + "\n".join(body) + "\n</svg>\n")


if __name__ == "__main__":
    (HERE / "name-card.svg").write_text(build(guides=True))
    (HERE / "name-card-print.svg").write_text(build(guides=False))
    _, (nx0, ny0, ny1) = card_outline()
    print(f"card {BOARD_W:.1f} x {CARD_H:.1f} mm; IR notch from x={nx0:.2f} to the right edge, "
          f"y={ny0:.2f}..{ny1:.2f} mm")
