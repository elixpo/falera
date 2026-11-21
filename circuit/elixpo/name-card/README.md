# Name card template

The base plane for the printed name card on the front of the Falera Badge: the
band between the board's top edge and the top of the LCD module. Measured from
`../elixpo.kicad_pcb`.

| File | What |
| --- | --- |
| `name-card.svg` | 1:1 in millimetres: the cut line (red), the cut-outs, a 2 mm safe area (green) and guides (grey dashes) for the IR parts, speaker grille, corner LEDs and the LCD's top edge |
| `name-card-print.svg` | the cut line and cut-outs only, for the final print |
| `make_name_card.py` | regenerates both; edit the numbers there if the board changes |

**Size:** 88.0 × 59.5 mm. The board's top edge to the top of the LCD module is
60.0 mm; the card stops 0.5 mm short of the LCD. Below the lanyard slots there
is 52.4 mm of height to the card's bottom edge.

**Cut-outs** (0.5 mm clearance):
- the two lanyard slots, so the lanyard passes through the card;
- a notch on the right edge, 13.3 × 8.5 mm (x 74.7–88.0, y 19.8–28.3), around
  the IR receiver (U8) and the IR LED (D10) together: both must see out, and
  they sit too close for a strip of paper between them.

**Guides only:** the speaker grille (G1) and the two amber corner LEDs (D1,
D2). The card can cover them, but paper over the grille muffles the speaker,
so keep the artwork light there or cut it out too.

To design: open `name-card.svg` in Inkscape, Illustrator or Figma (it keeps
its millimetre size), put the artwork inside the green safe area, and hide the
guides. Print `name-card-print.svg` at **100% / actual size** on photo paper and
check it against the board before cutting.
