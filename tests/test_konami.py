"""Host tests for the Konami code easter egg (oreoOS/konami.py) and its
place in the Badge app (apps/badge).

The waveform must be exactly the one printed on the owner's name card
(circuit/card/name-card/make_cards.py, wave()): the reference values below
were taken from that script's SVG output. If the card's algorithm ever
changes, change both sides and these numbers together.

    python3 -m unittest tests.test_konami -v
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_badge_app as tb  # noqa: E402  (the Badge app's stub environment)

UP, DOWN, LEFT, RIGHT, B, A = 3, 4, 5, 6, 2, 1
CODE = (UP, UP, DOWN, DOWN, LEFT, RIGHT, LEFT, RIGHT, B, A)

# From make_cards.py: the card's bar heights for @anwe-ch (12 mm band,
# height = max(0.5, (0.15 + 0.85 * v) * 12)), its hash caption and red bars.
CARD_HEIGHTS_ANWE = [
    6.920, 7.240, 5.540, 4.660, 7.790, 8.680, 7.620, 9.840, 9.940, 6.920,
    6.420, 8.010, 8.970, 7.940, 7.780, 8.400, 6.790, 6.750, 9.350, 9.560,
    6.800, 4.130, 4.560, 7.920, 10.280, 8.810, 5.330, 5.370, 6.760, 5.770,
    5.870, 6.790, 7.090, 8.020, 8.280, 6.360, 5.920, 7.400, 8.800, 9.350,
    8.330, 6.920, 6.100, 7.170, 7.220, 6.440, 6.940, 7.860, 8.590, 7.530,
    8.070, 10.660, 11.580, 11.400, 9.110, 5.860, 6.520, 9.120, 10.420, 10.690,
    9.270, 8.610, 9.900, 8.680]
CARDS = {   # handle: (sha256 caption on the card, its red bars)
    "anwe-ch": ("3ace4a14", [51, 52, 53]),
    "Circuit-Overtime": ("cfca9048", [11, 12, 13]),
    "nihal-gazi": ("d5331b85", [24, 25, 26]),
    "ez-vivek": ("5c620c21", [58, 59, 60]),
}


def load_konami():
    api = types.ModuleType("oreoOS.api")
    api.BTN_A, api.BTN_B, api.BTN_UP, api.BTN_DOWN, api.BTN_LEFT, api.BTN_RIGHT = A, B, UP, DOWN, LEFT, RIGHT
    pkg = types.ModuleType("oreoOS")
    pkg.__path__ = []
    pkg.api = api
    saved = {k: sys.modules.get(k) for k in ("oreoOS", "oreoOS.api")}
    sys.modules.update({"oreoOS": pkg, "oreoOS.api": api})
    try:
        spec = importlib.util.spec_from_file_location("konami_t", ROOT / "oreoOS/konami.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.Detector()          # builds the sequence from api while it is installed
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


K = load_konami()


class FakeAudio:
    """oreoWare.audio.Audio's surface: a ring of `size` bytes that the test
    drains by hand."""

    def __init__(self, size=4096, available=True):
        self.available = available
        self.size = size
        self.held = 0
        self.written = bytearray()
        self.calls = []

    def start(self, rate=None):
        self.calls.append(("start", rate))

    def stop(self):
        self.calls.append(("stop",))

    def free(self):
        return self.size - self.held

    def buffered(self):
        return self.held

    def write(self, data, rate=None):
        assert rate == K.RATE
        n = min(len(data), self.free())
        self.written += bytes(data[:n])
        self.held += n
        return n

    def drain(self, n=None):
        self.held = 0 if n is None else max(0, self.held - n)


class Clock:
    def __init__(self):
        self.t = 1000

    def __call__(self):
        return self.t


class WaveformMatchesTheCard(unittest.TestCase):

    def test_bar_heights_equal_the_cards(self):
        got = [round(max(0.5, (0.15 + 0.85 * v) * 12), 3) for v in K.wave("anwe-ch")]
        self.assertEqual(got, CARD_HEIGHTS_ANWE)

    def test_hash_caption_and_red_bars_for_every_card(self):
        for handle, (caption, hot) in CARDS.items():
            vals = K.wave(handle)
            self.assertEqual(len(vals), 64)
            self.assertEqual(K.digest(handle)[:4].hex(), caption, handle)
            p = K.peak(vals)
            self.assertEqual([p - 1, p, p + 1], hot, handle)

    def test_handle_case_does_not_matter(self):
        self.assertEqual(K.wave("Anwe-CH"), K.wave("anwe-ch"))


class DetectorTest(unittest.TestCase):

    def feed(self, presses):
        d = K.Detector(CODE)
        return [d.feed(b) for b in presses], d

    def test_the_code_fires_on_the_final_a(self):
        out, _ = self.feed(CODE)
        self.assertEqual(out, [False] * 9 + [True])

    def test_extra_presses_before_still_work(self):
        out, _ = self.feed((A, B, UP) + CODE)
        self.assertTrue(out[-1])
        self.assertEqual(sum(out), 1)

    def test_a_wrong_press_breaks_it(self):
        out, _ = self.feed(CODE[:5] + (UP,) + CODE[5:])
        self.assertFalse(any(out))

    def test_it_fires_again(self):
        out, _ = self.feed(CODE + CODE)
        self.assertEqual(sum(out), 2)

    def test_default_sequence_is_from_api(self):
        self.assertEqual(K.Detector(CODE).seq, CODE)


class NotesTest(unittest.TestCase):

    def test_sixty_four_notes_on_the_scale(self):
        ns = K.notes("anwe-ch")
        self.assertEqual(len(ns), 64)
        allowed = [K.BASE_HZ * 2 ** (s / 12) for s in K.SCALE]
        for hz, ms, acc in ns:
            self.assertTrue(any(abs(hz - a) < 1e-6 for a in allowed), hz)
        hz = [n[0] for n in ns]
        vals = K.wave("anwe-ch")
        self.assertAlmostEqual(hz[vals.index(min(vals))], K.BASE_HZ)          # lowest bar, C4
        self.assertAlmostEqual(hz[vals.index(max(vals))], K.BASE_HZ * 4)      # highest, C6

    def test_red_bars_are_the_accents(self):
        ns = K.notes("anwe-ch")
        acc = [k for k, n in enumerate(ns) if n[2]]
        self.assertEqual(acc, [51, 52, 53])
        self.assertTrue(all(ns[k][1] == K.ACCENT_MS for k in acc))
        self.assertTrue(all(n[1] == K.NOTE_MS for k, n in enumerate(ns) if k not in acc))

    def test_note_pcm_whole_periods_from_zero(self):
        pcm = K.note_pcm(440.0, 100, K.AMP)
        period = round(K.RATE / 440.0)
        gap = 2 * (K.GAP_MS * K.RATE // 1000)
        body = len(pcm) - gap
        self.assertEqual(body % (2 * period), 0)
        self.assertEqual(pcm[:2], b"\x00\x00")
        self.assertEqual(pcm[body:], bytes(gap))
        import struct
        samples = struct.unpack("<%dh" % (body // 2), pcm[:body])
        self.assertLessEqual(max(samples), K.AMP)
        self.assertGreaterEqual(min(samples), -K.AMP)


class TuneTest(unittest.TestCase):

    def test_plays_everything_without_overfilling_then_stops(self):
        audio, clock = FakeAudio(size=4096), Clock()
        ns = K.notes("ez-vivek")
        t = K.Tune(audio, ns, now_ms=clock)
        t.start()
        self.assertEqual(audio.calls[0], ("start", K.RATE))
        self.assertEqual(t.playhead(), 0)
        heads = set()
        for _ in range(5000):
            self.assertLessEqual(audio.held, audio.size)
            clock.t += 20
            audio.drain(4096)
            t.update()
            heads.add(t.playhead())
            if t.done:
                break
        self.assertTrue(t.done)
        self.assertEqual(audio.calls.count(("stop",)), 1)
        want = b"".join(K.note_pcm(hz, ms, K.ACCENT_AMP if acc else K.AMP) for hz, ms, acc in ns)
        self.assertEqual(bytes(audio.written), want)
        self.assertTrue(set(range(64)) <= heads)        # the playhead visited every bar

    def test_waits_for_the_ring_to_drain(self):
        audio, clock = FakeAudio(size=1 << 22), Clock()   # everything fits at once
        t = K.Tune(audio, K.notes("anwe-ch"), now_ms=clock)
        t.start()
        clock.t += t.length_ms + 10
        t.update()
        self.assertFalse(t.done)        # still sounding
        audio.drain()
        t.update()
        self.assertTrue(t.done)

    def test_silent_without_a_speaker(self):
        for audio in (None, FakeAudio(available=False)):
            clock = Clock()
            t = K.Tune(audio, K.notes("anwe-ch"), now_ms=clock)
            self.assertTrue(t.silent)
            t.start()
            clock.t += t.length_ms // 2
            t.update()
            self.assertGreater(t.playhead(), 0)
            clock.t += t.length_ms
            t.update()
            self.assertTrue(t.done)
            if audio is not None:
                self.assertEqual(audio.calls, [])

    def test_stop_is_idempotent(self):
        audio = FakeAudio()
        t = K.Tune(audio, K.notes("anwe-ch"), now_ms=Clock())
        t.start()
        t.stop()
        t.stop()
        self.assertEqual(audio.calls.count(("stop",)), 1)
        self.assertEqual(t.playhead(), -1)


class BadgeKonamiTest(unittest.TestCase):
    """The code on the Badge app, through the app's own stub OS."""

    def setUp(self):
        self.m, self.pkg, self.modules = tb.build_env(False)
        self.inst = tb._Installed(self.modules)
        self.inst.__enter__()
        gh = self.pkg.github
        gh.online = lambda: False
        gh.cached_profile = lambda login: (dict(tb.FULL, login=login), 10)
        self.refreshes = []
        gh.fetch_profile = lambda login: self.refreshes.append(login)

    def tearDown(self):
        self.inst.__exit__()

    def app(self, profile, audio=None):
        self.pkg.state.files = {"/profile.json": profile}
        self.pkg.profile.reload()
        app = self.m.App()
        app.on_enter(types.SimpleNamespace(audio=audio))
        return app

    def enter_code(self, app):
        for b in CODE:
            app.on_button_press(b)

    def texts(self, app):
        d = tb.FakeDisplay()
        app._dirty = True
        app.draw(d)
        return d, [t[0] for t in d.texts]

    def test_code_plays_the_owners_waveform(self):
        audio = FakeAudio()
        app = self.app({"GITHUB_USER": "anwe-ch", "DISPLAY_NAME": "Anwesha"}, audio)
        self.enter_code(app)
        self.assertEqual(app._kmode, "play")
        self.assertEqual(audio.calls[0], ("start", K.RATE))
        self.assertGreater(len(audio.written), 0)
        self.assertEqual(app._kvals, K.wave("anwe-ch"))
        self.assertEqual(self.refreshes, [])            # the final A didn't refresh
        d, texts = self.texts(app)
        self.assertIn("KONAMI  @anwe-ch", texts)
        self.assertIn(self.m.HINT_KONAMI, texts)
        bars = [o for o in d.ops if o[0] == "rect" and not o[5]]
        self.assertGreaterEqual(len(bars), 64)
        m = self.m
        for kind, x, y, w, h, chrome in d.ops:          # inside the card (+ its 2 px shadow)
            if chrome:
                continue
            self.assertGreaterEqual(x, m.CARD_X)
            self.assertLessEqual(x + w, m.CARD_X + m.CARD_W + 2)
            self.assertGreaterEqual(y, m.CARD_Y)
            self.assertLessEqual(y + h, m.CARD_Y + m.CARD_H + 2)
        box = tb.FakeDisplay()                         # the waveform alone: in its box
        app._draw_konami(box)
        self.assertGreaterEqual(len(box.ops), 64)
        for kind, x, y, w, h, chrome in box.ops:
            self.assertGreaterEqual(x, m.IN_X)
            self.assertLessEqual(x + w, m.IN_R)
            self.assertGreaterEqual(y, m.DIV_Y + 6)
            self.assertLessEqual(y + h, m.CARD_Y + m.CARD_H - 6)

    def test_b_stops_it_and_exit_stops_it(self):
        audio = FakeAudio()
        app = self.app({"GITHUB_USER": "anwe-ch"}, audio)
        self.enter_code(app)
        app.on_button_press(B)
        self.assertIsNone(app._kmode)
        self.assertIn(("stop",), audio.calls)
        self.enter_code(app)
        app.on_exit()
        self.assertEqual(audio.calls.count(("stop",)), 2)

    def test_no_github_user_says_how_to_set_one(self):
        app = self.app({"DISPLAY_NAME": "Mona Lisa"})
        self.enter_code(app)
        self.assertEqual(app._kmode, "nouser")
        _, texts = self.texts(app)
        self.assertIn("Konami code!", texts)
        self.assertIn("falera setup --set GITHUB_USER=you", texts)
        app.on_button_press(A)                          # any button puts the card back
        self.assertIsNone(app._kmode)

    def test_no_profile_at_all_still_answers(self):
        app = self.app({})
        self.enter_code(app)
        _, texts = self.texts(app)
        self.assertIn("Konami code!", texts)

    def test_silent_badge_shows_no_speaker(self):
        app = self.app({"GITHUB_USER": "anwe-ch"}, audio=None)
        self.enter_code(app)
        self.assertTrue(app._tune.silent)
        _, texts = self.texts(app)
        self.assertIn("no speaker", texts)

    def test_a_still_refreshes_normally(self):
        self.pkg.github.online = lambda: True
        app = self.app({"GITHUB_USER": "anwe-ch"})
        app.on_button_press(A)
        self.assertIsNone(app._kmode)


class ShippedTest(unittest.TestCase):

    def test_konami_is_deployed_and_released(self):
        deploy = (ROOT / "tools/deploy.py").read_text()
        release = (ROOT / "tools/build_release.py").read_text()
        self.assertIn('"oreoOS/konami.py"', deploy)
        self.assertIn('"oreoOS/konami.py"', release)


if __name__ == "__main__":
    unittest.main()
