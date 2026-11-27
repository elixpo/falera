# The stall demo: one run, about 3 minutes

One focused demo, the same every time. The poster is on the table; the
laptop shows the stall screen; the badge does the talking.

## Set up the laptop before people arrive

1. **Browser, full screen (F11)**, three tabs in this order:
   1. `events/abp-infocom-2026/stall-screen.html`. Keys **1–5** switch its
      views: **1** the overview (leave it on between demos), **2** the three
      layers, **3** what it does (updates over the air, infrared, the RV565
      video format), **4** the research paper, **5** star and back it.
   2. https://arxiv.org/abs/2610.35935 (the paper itself, if someone wants
      to see the PDF).
   3. https://falera.elixpo.com/badge/ (the 3D board, as a backup if the
      badge misbehaves).
2. **Terminal, big font (Ctrl + several times)**, split in two:
   - left: `export OREO_PORT=/dev/ttyACM0` then `falera logs` (a live console;
     it only listens, the badge keeps running);
   - right: `export OREO_PORT=/dev/ttyACM0`, with this typed and ready:
     `falera transfer send ~/Pictures/lumi.png` (any small picture).
3. **Badge:** on the home screen, Wi-Fi connected, the Big Buck Bunny clip in
   the Gallery (`falera add` the night before).

Use only `falera transfer` and `falera logs` at the stall: every other
command restarts the badge for a few seconds.

## The run

| # | Say | Badge | Laptop |
| --- | --- | --- | --- |
| 1 | "This is a **computer you can wear round your neck**." | Hand it over, on the home screen | Tab 1, view **1** |
| 2 | **Why:** "Every event gives you a badge, and you throw it away in two days. We wanted one you keep, that does something, and that anyone can build for." | | view 1 |
| 3 | **Three parts:** "It's built like a real computer. At the bottom, **a kernel**: MicroPython firmware on the ESP32-S3, with our drivers. On top, **FaleraOS**, the operating system I wrote: the home screen, an App Store, updates over the air. And on top of that, **apps anyone can write** in Python." | | press **2** |
| 4 | **Examples:** "Here's the App Store… a game… and your GitHub card. It also updates itself over the air, like your phone, and talks infrared, to other badges and like a TV remote." | Open the App Store, then a game (Racer or Flappy Lumi), then the Badge app | press **3** |
| 5 | **Remote control:** "And your computer talks to it over USB, while it keeps running. Watch: I'll send it a picture." | Open **Transfer**; read the code on the screen aloud | Terminal: run the transfer, type the code; point at the live log on the left |
| 6 | | The picture arrives; open it in the Gallery | |
| 7 | **The paper:** "Video was the hard part: this chip managed half a frame a second. So I invented a format, RV565, that sends only what changed. It's a research paper now." | | press **4** |
| 8 | **The video:** "And here it is, on the badge." | Gallery → play the Big Buck Bunny clip | press **3** (the 0.5 → 24 card) |
| 9 | **The ask:** "If you like it, **star the repo**: it helps more than you'd think. And if you want to help build the first boards, ₹100 buys the buttons for one badge." | | press **5** (the two QR codes) |

Then hand over a QR card and go back to view 1.

## Words to keep right

- Video: "half a frame a second to 24 frames a second" is the firmware path;
  inside FaleraOS it's about 12. Say "smooth video", or give both numbers if
  asked.
- The paper is a **preprint** on arXiv; Adafruit **featured** RV565.
- OSHWA certified the **board design**.
- It's **Falera** and **FaleraOS**; the panda is **Lumi**.

## If something goes wrong

| Problem | Do |
| --- | --- |
| Transfer says the badge didn't answer | Is the Transfer app open with a code on screen? Leave it (HOME), open it again, retry. |
| The badge hangs | Press RESET; it boots in a few seconds. Keep talking over view 2. |
| No Wi-Fi at the venue | Skip the App Store install; everything else works offline. Phone hotspot is saved. |
| The badge won't start | Switch to tab 3 (the 3D board) and do the demo from the poster. |
