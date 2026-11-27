"""The live link: `falera` commands answered by a running FaleraOS.

The badge side is the real oreoOS/livelink.py, loaded from the checkout
and wired to the CLI through LiveSerial, an in-memory USB line. It runs
over FakeTransport's pretend badge (its files, Wi-Fi, App Store), so a
command can be checked both ways: live (FaleraOS never stopped: no Ctrl-C,
no raw REPL, no restart) and through the raw REPL fallback when the badge
does not answer the hello (an older FaleraOS). Skipped outside a FaleraOS
checkout.
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeClock, run_cli

from fake import FakeTransport
from oreo_cli import live

REPO = Path(__file__).resolve().parents[2]
LIVELINK = REPO / "oreoOS" / "livelink.py"
STDIN_RING = 259


def load_livelink(name="livelink_cli_under_test"):
    spec = importlib.util.spec_from_file_location(name, LIVELINK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LiveSerial:
    """The USB line. mode 'live': FaleraOS with the live link reads it
    every frame. 'old': an older FaleraOS, nothing reads it (bytes go into
    MicroPython's 259-byte stdin ring; Ctrl-C is seen only as a byte
    enters it). 'transfer': the Transfer app holds it and announces."""

    def __init__(self, srv, mode="live", damage_data=None):
        self.srv = srv
        self.mode = mode
        self.to_host = bytearray()
        self.inbox = bytearray()
        self.written = bytearray()
        self.ring = bytearray()
        self.blocked = 0
        self.interrupted = False
        self.damage_data = damage_data   # corrupt this @OLD seq once
        self.timeout = 0.01
        self.lines = []
        if mode == "transfer":
            self.to_host += b'@OTB {"op":"announce","v":1,"app":"transfer"}\r\n'

    def out(self, text):
        raw = (text + "\r\n").encode()
        if self.damage_data is not None and raw.startswith(b"@OLD %d " % self.damage_data):
            raw = raw[:-12] + b"AAAAAAAAAA\r\n"
            self.damage_data = None
        self.to_host += raw

    def _frames(self, n=3):
        if self.mode == "live":
            for _ in range(n):
                self.srv.tick(20)

    # pyserial surface
    def write(self, data):
        self.written += data
        if self.mode == "old":
            for b in data:
                if self.blocked or len(self.ring) >= STDIN_RING:
                    self.blocked += 1
                elif b == 3:
                    self.interrupted = True
                else:
                    self.ring.append(b)
            return len(data)
        if self.mode != "live":
            return len(data)
        self.inbox += data
        while b"\n" in self.inbox:
            i = self.inbox.index(b"\n")
            line = bytes(self.inbox[:i]).rstrip(b"\r")
            del self.inbox[:i + 1]
            self.lines.append(line)
            self.srv.feed(line)
        self._frames()
        return len(data)

    @property
    def in_waiting(self):
        self._frames()
        return len(self.to_host)

    def inWaiting(self):
        return self.in_waiting

    def read(self, n=1):
        self._frames()
        data = bytes(self.to_host[:n])
        del self.to_host[:n]
        return data

    def flush(self):
        pass


class LiveFake(FakeTransport):
    """FakeTransport (the raw REPL path) that also has the USB line the
    live link uses, served by the real oreoOS/livelink.py over the same
    pretend badge."""

    def __init__(self, mode="live", version=None, damage_data=None, **kw):
        super().__init__(**kw)
        self.L = load_livelink()
        if version is not None:
            self.L.VERSION = version
        self.L._os = self.os
        osm = self.os
        ms = [0]

        def clock():                     # FaleraOS frames pass as the CLI waits
            ms[0] += 50
            return ms[0]
        self.srv = self.L.Server(self._out, threaded=False, os_version=self.config.VERSION,
                                 clock=clock,
                                 opener=lambda p, m="r": open(osm._real(p), m))
        self.serial = LiveSerial(self.srv, mode, damage_data)
        self.raw_opens = 0
        w = self.wifi                    # the driver's non-blocking auto-connect
        w.auto_begin = lambda: w.connect_from_config()
        w.auto_tick = lambda: False
        w.auto_state = lambda: "connected" if w.connected else "no_network"

    def _out(self, text):
        self.serial.out(text)

    def raw_serial(self):
        self.raw_opens += 1
        self.closed = False
        return self.serial

    def badge_modules(self):
        return {k: v for k, v in self._modules().items()
                if k.split(".")[0] in ("oreoOS", "oreoWare")}


@unittest.skipUnless(LIVELINK.is_file(), "needs a FaleraOS checkout")
class _LiveCase(unittest.TestCase):
    mode = "live"

    def setUp(self):
        self.fake = LiveFake(self.mode)
        self.addCleanup(self.fake.cleanup)
        p = mock.patch.dict(sys.modules, self.fake.badge_modules())
        p.start()
        self.addCleanup(p.stop)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        env = mock.patch.dict(os.environ, {"OREO_NO_LIVE": ""})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv, **kw):
        return run_cli(list(argv), fake=self.fake, **kw)

    def assert_kept_running(self, r):
        self.assertEqual(r.rc, 0, r.out)
        self.assertEqual(self.fake.enters, 0, "FaleraOS was stopped")
        self.assertEqual(self.fake.restarts, [], "FaleraOS was restarted")
        self.assertEqual(self.fake.sent, [], "the raw REPL was used")
        self.assertNotIn(b"\x03", bytes(self.fake.serial.written))
        self.assertTrue(self.fake.closed)
        self.assertIn(b'"op":"bye"', bytes(self.fake.serial.written))

    def local(self, name, data):
        p = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p


class LiveCommandsTest(_LiveCase):
    def test_info(self):
        r = self.cli("info")
        self.assert_kept_running(r)
        self.assertIn("v2.0.0", r.out)
        self.assertIn("live link v1", r.out)

    def test_ls(self):
        self.fake.put("/documents/a.txt", b"abc")
        r = self.cli("ls", "/documents")
        self.assert_kept_running(r)
        self.assertIn("a.txt", r.out)
        r = self.cli("ls", "/nope")
        self.assertEqual(r.rc, 1)
        self.assertIn("not found on the badge", r.out)

    def test_push_and_pull_round_trip(self):
        data = os.urandom(9000)
        src = self.local("blob.bin", data)
        r = self.cli("push", src, "/documents/")
        self.assert_kept_running(r)
        self.assertEqual(self.fake.get("/documents/blob.bin"), data)
        self.assertFalse(self.fake.exists("/documents/blob.bin.part"))
        out = os.path.join(self.dir, "back.bin")
        r = self.cli("pull", "/documents/blob.bin", out)
        self.assert_kept_running(r)
        self.assertEqual(Path(out).read_bytes(), data)

    def test_push_folder(self):
        self.local("pkg/a.py", b"A")
        self.local("pkg/sub/b.py", b"B" * 3000)
        r = self.cli("push", "-r", os.path.join(self.dir, "pkg"), "/remote/pkg")
        self.assert_kept_running(r)
        self.assertEqual(self.fake.get("/remote/pkg/sub/b.py"), b"B" * 3000)

    def test_push_into_faleraos_says_it_needs_a_restart(self):
        r = self.cli("push", self.local("x.py", b"pass\n"), "/oreoOS/x.py")
        self.assert_kept_running(r)
        self.assertIn("restart it to use the new files", r.out)

    def test_pull_folder(self):
        self.fake.put("/documents/d/one.txt", b"1")
        self.fake.put("/documents/d/two.txt", b"22")
        out = os.path.join(self.dir, "copy")
        r = self.cli("pull", "-r", "/documents/d", out)
        self.assert_kept_running(r)
        self.assertEqual(Path(out, "two.txt").read_bytes(), b"22")

    def test_rm(self):
        self.fake.put("/documents/old.mdz", b"x")
        r = self.cli("rm", "/documents/old.mdz")
        self.assert_kept_running(r)
        self.assertFalse(self.fake.exists("/documents/old.mdz"))
        self.fake.put("/documents/dir/x", b"x")
        r = self.cli("rm", "/documents/dir")
        self.assertEqual(r.rc, 1)
        self.assertIn("is a directory", r.out)
        r = self.cli("-y", "rm", "-r", "/documents/dir")
        self.assertEqual(r.rc, 0, r.out)
        self.assertFalse(self.fake.exists("/documents/dir"))

    def test_screenshot_of_the_running_screen(self):
        frame = bytes((i * 7) & 0xFF for i in range(320 * 240 * 2))
        disp = types.ModuleType("oreoWare.display")
        dobj = types.SimpleNamespace(_buf=bytearray(frame), _dirty=False)
        disp.current = lambda: dobj
        sys.modules["oreoWare.display"] = disp
        sys.modules["oreoWare"].display = disp
        target = os.path.join(self.dir, "shot.png")
        r = self.cli("screenshot", target)
        self.assert_kept_running(r)
        from oreo_cli import screenshot
        self.assertEqual(Path(target).read_bytes(), screenshot.to_png(frame, 320, 240))
        self.assertNotIn("half drawn", r.out)

    def test_apps_list_install_update_remove(self):
        self.fake.wifi.saved = [{"ssid": "Home", "password": "hunter2!", "priority": 1}]
        r = self.cli("apps", "install", "snake")
        self.assert_kept_running(r)
        self.assertIn("snake installed", r.out)
        self.assertTrue(self.fake.exists("/apps/snake/main.py"))
        r = self.cli("apps")
        self.assert_kept_running(r)
        self.assertIn("Snake", r.out)
        r = self.cli("-y", "apps", "remove", "snake")
        self.assert_kept_running(r)
        self.assertFalse(self.fake.exists("/apps/snake"))

    def test_apps_install_offline(self):
        r = self.cli("apps", "install", "snake")
        self.assertEqual(r.rc, 1)
        self.assertIn("could not get online", r.out)
        self.assertEqual(self.fake.enters, 0)

    def test_wifi(self):
        r = self.cli("wifi", "scan")
        self.assert_kept_running(r)
        self.assertIn("Home", r.out)
        with mock.patch("sys.stdin", io.StringIO("hunter2!\n")):
            r = self.cli("wifi", "connect", "Home", "--password-stdin")
        self.assert_kept_running(r)
        self.assertIn("connected to Home", r.out)
        self.assertNotIn("hunter2!", r.out)
        r = self.cli("wifi", "saved")
        self.assert_kept_running(r)
        self.assertIn("Home", r.out)
        r = self.cli("wifi", "forget", "Home")
        self.assert_kept_running(r)
        self.assertEqual(self.fake.wifi.saved, [])

    def test_setup_writes_the_profile_live(self):
        r = self.cli("setup", "--name", "Mona Lisa", "--timezone", "5.5", "--no-avatar")
        self.assert_kept_running(r)
        self.assertIn("FaleraOS kept running", r.out)
        saved = json.loads(self.fake.get("/profile.json"))
        self.assertEqual(saved["DISPLAY_NAME"], "Mona Lisa")
        self.assertEqual(saved["TIMEZONE_OFFSET"], 5.5)
        r = self.cli("setup", "--show")
        self.assert_kept_running(r)
        self.assertIn("Mona Lisa", r.out)

    def test_setup_avatar_owner_and_removal(self):
        from oreo_cli import avatar
        self.fake.put(avatar.PATH, avatar.MAGIC + bytes((2, 2, 7)) + b"octocat" + b"\0" * 8)
        r = self.cli("setup", "--show")
        self.assert_kept_running(r)
        self.assertIn("avatar of @octocat", r.out)
        r = self.cli("setup", "--github-user", "")
        self.assert_kept_running(r)
        self.assertFalse(self.fake.exists(avatar.PATH))

    def test_add_a_document(self):
        src = self.local("notes.md", b"# Hello\n\nfrom the computer\n")
        r = self.cli("add", src)
        self.assert_kept_running(r)
        self.assertTrue(self.fake.exists("/documents/notes.mdz"))

    def test_app_install_from_a_folder(self):
        from oreo_cli import devtools
        folder = Path(self.dir, "my_game")
        devtools.scaffold(folder, "my_game", "My Game", "tester")
        r = self.cli("app", "install", str(folder))
        self.assert_kept_running(r)
        self.assertTrue(self.fake.exists("/apps/my_game/manifest.json"))
        self.assertIn("FaleraOS kept running", r.out)

    def test_update_still_uses_the_raw_repl(self):
        r = self.cli("update", "--check")
        self.assertEqual(self.fake.raw_opens, 0)
        self.assertGreaterEqual(self.fake.enters, 1)


class ForceOldPathTest(_LiveCase):
    def assert_old_path(self, r):
        self.assertEqual(r.rc, 0, r.out)
        self.assertEqual(self.fake.raw_opens, 0)
        self.assertEqual(self.fake.enters, 1)
        self.assertEqual(self.fake.restarts[-1:], ["soft"])

    def test_no_live_flag(self):
        self.assert_old_path(self.cli("--no-live", "ls", "/"))

    def test_explicit_reset(self):
        r = self.cli("--reset", "soft", "ls", "/")
        self.assert_old_path(r)
        r = self.cli("--reset", "none", "ls", "/")
        self.assertEqual(self.fake.raw_opens, 0)
        self.assertEqual(self.fake.restarts[-1], "none")

    def test_environment(self):
        with mock.patch.dict(os.environ, {"OREO_NO_LIVE": "1"}):
            self.assert_old_path(self.cli("ls", "/"))


class OlderFaleraOSTest(_LiveCase):
    """No live link on the badge: the hello goes unanswered and the CLI
    falls back to the raw REPL, as for every FaleraOS before it."""
    mode = "old"

    def test_falls_back_to_the_raw_repl(self):
        self.fake.put("/documents/a.txt", b"abc")
        r = self.cli("-v", "ls", "/documents")
        self.assertEqual(r.rc, 0, r.out)
        self.assertIn("a.txt", r.out)
        self.assertEqual(self.fake.enters, 1)
        self.assertEqual(self.fake.restarts, ["soft"])
        self.assertIn("no live link", r.out)

    def test_hellos_leave_room_for_ctrl_c_and_are_comments(self):
        self.cli("ls", "/")
        ser = self.fake.serial
        self.assertEqual(ser.blocked, 0)
        self.assertLess(len(ser.ring), STDIN_RING // 2)     # Ctrl-C and more still fit
        text = bytes(ser.ring).decode("ascii")
        compile(text, "<repl>", "exec")          # what the REPL reads: comments
        for line in text.splitlines():
            self.assertTrue(not line.strip() or line.startswith("#"), line)

    def test_screenshot_falls_back(self):
        r = self.cli("screenshot", os.path.join(self.dir, "x.png"))
        self.assertEqual(self.fake.enters, 1)    # through the raw REPL (old FaleraOS)


class VersionMismatchTest(_LiveCase):
    def setUp(self):
        super().setUp()
        self.fake.L.VERSION = 99

    def test_other_version_falls_back(self):
        r = self.cli("-v", "ls", "/")
        self.assertEqual(r.rc, 0, r.out)
        self.assertEqual(self.fake.enters, 1)
        self.assertIn("live link v99", r.out)


class TransferOpenTest(_LiveCase):
    mode = "transfer"

    def test_says_to_leave_transfer(self):
        r = self.cli("info")
        self.assertEqual(r.rc, 4, r.out)
        self.assertIn("Transfer app is open", r.out)
        self.assertIn("HOME", r.out)
        self.assertEqual(self.fake.enters, 0)


class ClientTest(_LiveCase):
    def badge(self):
        clock = FakeClock()
        b = live.connect(self.fake, clock=clock.monotonic, sleep=clock.sleep)
        self.assertIsNotNone(b)
        return b

    def test_ctrl_c_mid_file_aborts_and_drops_the_part_file(self):
        b = self.badge()
        calls = []

        def progress(done, total):
            calls.append(done)
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            b.write_file(os.urandom(20000), "/documents/big.bin", progress=progress)
        self.assertIn(b'"op":"abort"', bytes(self.fake.serial.written))
        self.assertIsNone(self.fake.srv.task)
        self.assertFalse(self.fake.exists("/documents/big.bin.part"))
        self.assertFalse(self.fake.exists("/documents/big.bin"))
        # the link still works afterwards
        self.assertEqual(b.stat("/documents"), {"dir": True, "size": 0})

    def test_damaged_line_is_caught_and_read_again(self):
        data = os.urandom(10000)
        self.fake.put("/documents/d.bin", data)
        self.fake.serial.damage_data = 1
        b = self.badge()
        self.assertEqual(b.read_file("/documents/d.bin"), data)

    def test_log_lines_between_replies_are_ignored(self):
        b = self.badge()
        self.fake.serial.to_host += b"[net] link up\r\n[wifi] connecting\r\n"
        self.assertEqual(b.free()[0] > 0, True)
        self.assertGreaterEqual(b.c.noise, 2)

    def test_hello_lists_the_ops(self):
        b = self.badge()
        self.assertTrue(b.supports("put", "get", "screen", "profile_write"))
        self.assertFalse(b.supports("teleport"))

    def test_the_cli_and_badge_agree_on_the_wire(self):
        L = self.fake.L
        self.assertEqual(live.PROTOCOL, L.VERSION)
        self.assertEqual(live.HOST, L.HOST)
        self.assertEqual(live.HOST_DATA, L.HOST_DATA)
        self.assertEqual(live.BADGE, L.BADGE.encode())
        self.assertEqual(live.BADGE_DATA, L.BADGE_DATA.encode())

    def test_a_badge_without_a_raw_serial_is_not_tried(self):
        self.assertIsNone(live.connect(FakeTransport()))


if __name__ == "__main__":
    unittest.main()
