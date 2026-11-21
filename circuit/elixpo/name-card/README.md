# Name card template

The base plane for the printed name card on the front of the Falera Badge: the
band between the board's top edge and the top of the LCD module. Measured from
`../elixpo.kicad_pcb`.

| File | What |
| --- | --- |
| `name-card.svg` | 1:1 in millimetres: the cut line (red), the cut-outs, a 2 mm safe area (green), guides (grey dashes) for the IR parts, speaker grille, corner LEDs and the LCD's top edge, blue dimension lines, and a table of every feature's position and size |
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

**Covered by the card:** the speaker grille (G1) and the two amber corner LEDs
(D1, D2). They are marked with their sizes so the artwork can allow for them.

To design: open `name-card.svg` in Inkscape, Illustrator or Figma (it keeps
its millimetre size), put the artwork inside the green safe area, and hide the
guides. Print `name-card-print.svg` at **100% / actual size** on photo paper and
check it against the board before cutting.

## Every dimension (mm, from the card's top-left corner, x right, y down)

| Feature | x | y | width | height | Note |
| --- | ---: | ---: | ---: | ---: | --- |
| Card | 0.00 | 0.00 | 88.00 | 59.50 | cut line; top corners r 3.0 |
| Lanyard slot, left | 2.70 | 3.35 | 14.60 | 4.30 | cut; round ends r 2.15 |
| Lanyard slot, right | 70.70 | 3.35 | 14.60 | 4.30 | cut; round ends r 2.15 |
| IR notch (rx + tx) | 74.67 | 19.80 | 13.33 | 8.47 | cut, open to the right edge; inner corners r 1.0 |
| IR receiver U8 (body) | 82.25 | 20.30 | 5.44 | 7.47 | inside the notch |
| IR LED D10 | 75.17 | 23.83 | 4.66 | 2.34 | inside the notch |
| Speaker grille G1 | 71.40 | 9.10 | 13.50 | 10.80 | covered by the card |
| Corner LED D1 | 2.83 | 9.48 | 1.55 | 3.04 | covered; light shows through paper |
| Corner LED D2 | 85.62 | 7.48 | 1.55 | 3.04 | covered; light shows through paper |
| Artwork safe area | 2.00 | 9.65 | 66.90 | 47.85 | keep text and faces inside |

Cut-outs include 0.5 mm clearance; the part rows are the parts' own outlines.
