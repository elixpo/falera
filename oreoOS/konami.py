"""The Konami code easter egg: your name card's waveform, played as music.

Every Falera name card (circuit/card/name-card) carries a waveform drawn
from the owner's GitHub handle, and the hint "up up down down left right
left right B A". Entered on the Badge app, that code plays the same
waveform on the speaker, note by note.

The waveform is exactly the card's (circuit/card/name-card/make_cards.py,
wave()): SHA-256 of the lower-cased handle, then of the handle plus "#",
gives 64 bytes; each is smoothed with its neighbours as
(a[k-1] + 2 a[k] + a[k+1]) / 4, wrapping at the ends. The bar with the
highest value and its two neighbours are the card's red bars.

As music: each of the 64 values is one note, its height mapped onto a C
major pentatonic scale over two octaves (C4 to C6), lowest bar to lowest
note, highest to highest. Notes last NOTE_MS with GAP_MS of silence after
them; the three red bars last ACCENT_MS and play louder. A triangle wave,
22050 Hz mono: about eight seconds in all.

    from oreoOS import konami
    code = konami.Detector()
    if code.feed(btn):                      # in on_button_press
        tune = konami.Tune(os.audio, konami.notes(handle))
        tune.start()
    tune.update()                           # every frame, in App.update
    tune.playhead()                         # bar now playing, for drawing
    tune.stop()                             # in on_exit

Nothing here blocks: update() writes only what the audio driver's ring has
room for (oreoWare.audio.Audio.write never waits) and builds one note at a
time, so the heap holds at most one note's samples (under 10 KB). Without
a speaker (the breadboard badge, or an OS without oreoWare.audio) the tune
still runs on the clock, so the waveform animates in silence.
"""

import hashlib
import time

try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:          # CPython host tests
    def _ticks_ms():
        return int(time.monotonic() * 1000)

    def _ticks_diff(a, b):
        return a - b

try:
    from array import array
except ImportError:             # pragma: no cover
    array = None

N = 64                       # bars on the card
RATE = 22050
NOTE_MS = 110
ACCENT_MS = 230              # the card's red bars
GAP_MS = 15
AMP = 9000                   # int16 peak of a note
ACCENT_AMP = 15000
BASE_HZ = 261.63             # C4
SCALE = (0, 2, 4, 7, 9, 12, 14, 16, 19, 21, 24)   # pentatonic, semitones


def _sequence():
    from oreoOS import api
    return (api.BTN_UP, api.BTN_UP, api.BTN_DOWN, api.BTN_DOWN,
            api.BTN_LEFT, api.BTN_RIGHT, api.BTN_LEFT, api.BTN_RIGHT,
            api.BTN_B, api.BTN_A)


class Detector:
    """Watches button presses for the code. feed() is True on the press
    that completes it; a wrong press doesn't cost the presses that can
    still start it again (up up up down down ... works)."""

    def __init__(self, sequence=None):
        self.seq = tuple(sequence) if sequence is not None else _sequence()
        self.recent = []

    def feed(self, btn):
        self.recent.append(btn)
        if len(self.recent) > len(self.seq):
            self.recent.pop(0)
        if tuple(self.recent) == self.seq:
            self.recent = []
            return True
        return False


def digest(handle):
    """The 64 bytes the card's waveform is drawn from."""
    h = handle.lower().encode()
    return hashlib.sha256(h).digest() + hashlib.sha256(h + b"#").digest()


def wave(handle, n=N):
    """The card's 64 bar values, 0..1."""
    d = digest(handle)
    raw = [d[k] / 255 for k in range(n)]
    return [(raw[k - 1] + 2 * raw[k] + raw[(k + 1) % n]) / 4 for k in range(n)]


def peak(values):
    """Index of the highest bar (the first one, on a tie), as on the card."""
    best = 0
    for k in range(1, len(values)):
        if values[k] > values[best]:
            best = k
    return best


def notes(handle):
    """[(frequency Hz, length ms, accent)] for the handle's 64 bars."""
    vals = wave(handle)
    lo, hi = min(vals), max(vals)
    top = len(SCALE) - 1
    p = peak(vals)
    out = []
    for k, v in enumerate(vals):
        step = round((v - lo) / (hi - lo) * top) if hi > lo else top // 2
        hz = BASE_HZ * 2 ** (SCALE[step] / 12)
        accent = abs(k - p) <= 1
        out.append((hz, ACCENT_MS if accent else NOTE_MS, accent))
    return out


def note_pcm(hz, ms, amp, rate=RATE):
    """One note as int16 mono bytes: whole periods of a triangle wave that
    start and end at zero (no clicks between notes), then GAP_MS of
    silence."""
    period = max(2, round(rate / hz))
    q = period / 4
    one = []
    for i in range(period):
        if i < q:
            v = i / q
        elif i < 3 * q:
            v = 2 - i / q
        else:
            v = i / q - 4
        one.append(int(v * amp))
    if array is not None:
        cycle = bytes(array("h", one))
    else:                                   # pragma: no cover
        import struct
        cycle = struct.pack("<%dh" % period, *one)
    reps = max(1, round(ms * rate / 1000 / period))
    return cycle * reps + bytes(2 * (GAP_MS * rate // 1000))


class Tune:
    """Plays notes() on an oreoWare.audio.Audio, a little every frame."""

    def __init__(self, audio, tune_notes, now_ms=None):
        self.audio = audio if (audio is not None and getattr(audio, "available", False)) else None
        self.notes = tune_notes
        self._now = now_ms or _ticks_ms
        self.starts = []                    # each note's start, ms from the beginning
        t = 0
        for _hz, ms, _acc in tune_notes:
            self.starts.append(t)
            t += ms + GAP_MS
        self.length_ms = t
        self.i = 0                          # next note to build
        self.pending = None                 # memoryview of the note being written
        self.t0 = None
        self.done = False

    @property
    def silent(self):
        return self.audio is None

    def start(self):
        self.t0 = self._now()
        if self.audio is not None:
            self.audio.start(RATE)
        self.update()

    def update(self):
        """Top up the audio ring; finish when everything has played."""
        if self.done or self.t0 is None:
            return
        a = self.audio
        if a is not None:
            while True:
                if self.pending is None or len(self.pending) == 0:
                    if self.i >= len(self.notes):
                        break
                    hz, ms, accent = self.notes[self.i]
                    self.i += 1
                    self.pending = memoryview(note_pcm(hz, ms, ACCENT_AMP if accent else AMP))
                n = a.write(self.pending, RATE)
                if not n:
                    break
                self.pending = self.pending[n:]
        if self.elapsed() >= self.length_ms and (a is None or (
                self.i >= len(self.notes) and not self.pending and a.buffered() == 0)):
            self.stop()

    def elapsed(self):
        return 0 if self.t0 is None else _ticks_diff(self._now(), self.t0)

    def playhead(self):
        """The bar sounding now (0..63), by the clock; -1 before and after."""
        if self.t0 is None or self.done:
            return -1
        t = self.elapsed()
        k = -1
        for i, s in enumerate(self.starts):
            if s <= t:
                k = i
            else:
                break
        return k

    def stop(self):
        if self.done:
            return
        self.done = True
        self.pending = None
        if self.audio is not None:
            self.audio.stop()
