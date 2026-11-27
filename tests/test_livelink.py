"""Host tests for the live link (oreoOS/livelink.py): `falera` commands
answered by a running FaleraOS over the USB console.

The CLI end of the same protocol runs against this module in
cli/tests/test_live.py.

    python3 -m unittest tests.test_livelink -v
"""

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = load("oreoOS/livelink.py", "livelink_under_test")


class Clock:
    def __init__(self):
        self.ms = 10000

    def __call__(self):
        return self.ms


class Wire:
    """A Server rooted in a temp folder, plus every line it wrote."""

    def __init__(self, root, threaded=False):
        self.out = []
        self.clock = Clock()
        self.srv = L.Server(self.out.append, root=root, clock=self.clock,
                            threaded=threaded, os_version="v9.9.9")
        self.rid = 0

    def req(self, op, hello=True, **fields):
        self.rid += 1
        fields["op"] = op
        fields["id"] = self.rid
        self.srv.feed(L.HOST + json.dumps(fields).encode())
        return self.rid

    def hello(self):
        return self.req("hello", v=L.VERSION)

    def data(self, seq, piece):
        self.srv.feed(L.HOST_DATA + str(seq).encode() + b" " + base64.b64encode(piece))

    def msgs(self, rid=None):
        out = []
        for line in self.out:
            if line.startswith(L.BADGE):
                m = json.loads(line[len(L.BADGE):])
                if rid is None or m.get("id") == rid:
                    out.append(m)
        return out

    def last(self, rid=None):
        m = self.msgs(rid)
        return m[-1] if m else None

    def chunks(self):
        return [line for line in self.out if line.startswith(L.BADGE_DATA)]

    def run(self, frames=50):
        for _ in range(frames):
            self.srv.tick(L.WRITE_BUDGET_MS)
            if self.srv.task is None:
                return


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(self.tmp + "/documents")
        self.w = Wire(self.tmp)
        self.w.hello()

    def file(self, path, data):
        p = self.tmp + path
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def read(self, path):
        with open(self.tmp + path, "rb") as f:
            return f.read()

    def put(self, path, data, sha=True, **extra):
        rid = self.w.req("put", path=path, size=len(data),
                         sha256=hashlib.sha256(data).hexdigest() if sha else None, **extra)
        ready = self.w.last(rid)
        if ready["op"] != "ready":
            return rid, ready
        n = ready["chunk"]
        for seq, i in enumerate(range(0, len(data), n)):
            self.w.data(seq, data[i:i + n])
        self.w.run()
        return rid, self.w.last(rid)


class HandshakeTest(_Case):
    def test_hello_answers_version_os_and_ops(self):
        m = self.w.msgs(1)[0]
        self.assertEqual(m["op"], "hello")
        self.assertEqual(m["v"], L.VERSION)
        self.assertEqual(m["os"], "v9.9.9")
        self.assertIn("screen", m["ops"])
        self.assertIn("put", m["ops"])

    def test_wrong_version_is_refused(self):
        w = Wire(self.tmp)
        rid = w.req("hello", v=L.VERSION + 1)
        self.assertEqual(w.last(rid)["code"], "version")
        self.assertEqual(w.last(rid)["v"], L.VERSION)
        self.assertEqual(w.last(w.req("ls", path="/"))["code"], "hello")

    def test_requests_need_a_hello_first(self):
        w = Wire(self.tmp)
        self.assertEqual(w.last(w.req("ls", path="/"))["code"], "hello")

    def test_unknown_op_is_named_so_a_newer_cli_can_fall_back(self):
        m = self.w.last(self.w.req("teleport"))
        self.assertEqual((m["op"], m["code"]), ("error", "unknown"))

    def test_other_lines_are_ignored(self):
        before = len(self.w.out)
        for line in (b"", b"[net] link up", b">>> ", b"@OTH {\"op\":\"hello\"}", b"\x00\xff"):
            self.w.srv.feed(line)
        self.assertEqual(len(self.w.out), before)

    def test_unreadable_request(self):
        self.w.srv.feed(L.HOST + b"{not json")
        self.assertEqual(self.w.last()["code"], "proto")

    def test_ping_bye_and_session(self):
        self.assertTrue(self.w.srv.active())
        self.assertEqual(self.w.last(self.w.req("ping"))["op"], "pong")
        self.w.clock.ms += L.SESSION_MS + 1
        self.assertFalse(self.w.srv.active())
        self.assertEqual(self.w.last(self.w.req("bye"))["op"], "bye")
        self.assertEqual(self.w.last(self.w.req("ls", path="/"))["code"], "hello")

    def test_output_is_ascii_lines_without_ctrl_c(self):
        self.file("/documents/bin.dat", bytes(range(256)) * 20)
        self.w.req("get", path="/documents/bin.dat")
        self.w.run()
        self.w.req("ls", path="/documents")
        for line in self.w.out:
            raw = line.encode("ascii")          # raises on non-ASCII
            self.assertNotIn(b"\x03", raw)
            self.assertNotIn(b"\n", raw)

    def test_host_lines_are_python_comments(self):
        # A line that reaches the REPL instead (older FaleraOS) does nothing.
        for prefix in (L.HOST, L.HOST_DATA):
            compile(prefix.decode() + '{"op":"hello","v":1}\n', "<repl>", "exec")


class FilesTest(_Case):
    def test_stat_ls_free_mkdirs(self):
        self.file("/documents/a.txt", b"abc")
        self.assertEqual(self.w.last(self.w.req("stat", path="/documents/a.txt")),
                         {"op": "stat", "id": self.w.rid, "dir": False, "size": 3})
        self.assertEqual(self.w.last(self.w.req("stat", path="/nope"))["missing"], 1)
        ls = self.w.last(self.w.req("ls", path="/documents"))["ls"]
        self.assertEqual(ls, [["a.txt", 0, 3]])
        self.assertEqual(self.w.last(self.w.req("ls", path="/documents/a.txt"))["file"],
                         ["a.txt", 0, 3])
        self.assertEqual(self.w.last(self.w.req("ls", path="/zzz"))["code"], "missing")
        free = self.w.last(self.w.req("free"))
        self.assertGreater(free["total"], 0)
        self.w.req("mkdirs", path="/a/b/c")
        self.assertTrue(os.path.isdir(self.tmp + "/a/b/c"))

    def test_paths_must_be_absolute(self):
        self.assertEqual(self.w.last(self.w.req("stat", path="documents"))["code"], "path")

    def test_put_then_get_round_trip(self):
        data = os.urandom(10000)
        rid, done = self.put("/documents/x.bin", data, title="Receiving files", detail="x.bin")
        self.assertEqual(done["op"], "done")
        self.assertEqual(done["sha"], hashlib.sha256(data).hexdigest())
        acks = [m for m in self.w.msgs(rid) if m["op"] == "ack"]
        self.assertEqual(acks[-1]["got"], len(data))
        self.assertEqual(self.read("/documents/x.bin"), data)
        self.assertFalse(os.path.exists(self.tmp + "/documents/x.bin.part"))

        self.w.out.clear()
        rid = self.w.req("get", path="/documents/x.bin")
        self.assertEqual(self.w.last(rid)["size"], len(data))
        self.w.run()
        got = b"".join(base64.b64decode(c.split(" ", 2)[2]) for c in self.w.chunks())
        self.assertEqual(got, data)
        eof = self.w.last(rid)
        self.assertEqual((eof["op"], eof["n"], eof["sha"]),
                         ("eof", len(data), hashlib.sha256(data).hexdigest()))

    def test_get_streams_a_few_lines_per_frame(self):
        self.file("/documents/big.bin", b"z" * (L.CHUNK_OUT * 40))
        self.w.req("get", path="/documents/big.bin")
        with mock.patch.object(L, "_ticks", side_effect=range(0, 10 ** 6, 7)):
            self.w.srv.tick(L.WRITE_BUDGET_MS)
        self.assertLess(len(self.w.chunks()), 40)       # the frame went on
        self.assertIsNotNone(self.w.srv.task)
        self.w.run(200)
        self.assertEqual(len(self.w.chunks()), 40)

    def test_bad_hash_keeps_the_old_file(self):
        self.file("/documents/keep.txt", b"old")
        rid = self.w.req("put", path="/documents/keep.txt", size=3, sha256="0" * 64)
        self.w.data(0, b"new")
        self.assertEqual(self.w.last(rid)["code"], "hash")
        self.assertEqual(self.read("/documents/keep.txt"), b"old")
        self.assertFalse(os.path.exists(self.tmp + "/documents/keep.txt.part"))
        self.assertIsNone(self.w.srv.task)

    def test_out_of_order_or_oversized_chunks_fail_the_file(self):
        rid = self.w.req("put", path="/documents/a", size=10, sha256=None)
        self.w.data(1, b"x")
        self.assertEqual(self.w.last(rid)["code"], "seq")
        rid = self.w.req("put", path="/documents/a", size=2, sha256=None)
        self.w.data(0, b"abc")
        self.assertEqual(self.w.last(rid)["code"], "data")
        self.w.srv.feed(L.HOST_DATA + b"0 !!!!")
        self.assertEqual(self.w.last()["code"], "seq")       # no file on its way
        self.assertFalse(os.path.exists(self.tmp + "/documents/a"))

    def test_silent_computer_mid_file_times_out(self):
        rid = self.w.req("put", path="/documents/a", size=5000, sha256=None)
        self.w.data(0, b"x" * 100)
        self.w.clock.ms += L.PUT_IDLE_MS + 1
        self.w.run()
        self.assertEqual(self.w.last(rid)["code"], "timeout")
        self.assertFalse(os.path.exists(self.tmp + "/documents/a.part"))

    def test_empty_file(self):
        rid, done = self.put("/documents/empty", b"")
        self.assertEqual((done["op"], done["size"]), ("done", 0))
        self.assertEqual(self.read("/documents/empty"), b"")

    def test_no_space(self):
        with mock.patch.object(L.Server, "_free", return_value=(1000, 10 ** 6)):
            m = self.w.last(self.w.req("put", path="/documents/a", size=10, sha256=None))
        self.assertEqual(m["code"], "no_space")
        self.assertIsNone(self.w.srv.task)

    def test_busy_while_a_file_arrives_and_abort(self):
        self.w.req("put", path="/documents/a", size=5000, sha256=None)
        self.w.data(0, b"x" * 10)
        self.assertEqual(self.w.last(self.w.req("ls", path="/"))["code"], "busy")
        self.assertEqual(self.w.last(self.w.req("abort"))["op"], "aborted")
        self.assertIsNone(self.w.srv.task)
        self.assertFalse(os.path.exists(self.tmp + "/documents/a.part"))

    def test_a_new_hello_drops_a_half_file(self):
        self.w.req("put", path="/documents/a", size=5000, sha256=None)
        self.w.data(0, b"x" * 10)
        self.w.hello()
        self.assertIsNone(self.w.srv.task)
        self.assertFalse(os.path.exists(self.tmp + "/documents/a.part"))

    def test_over_long_line_fails_the_file(self):
        rid = self.w.req("put", path="/documents/a", size=5000, sha256=None)
        self.w.srv.feed(None)
        self.assertEqual(self.w.last(rid)["code"], "line")
        self.assertIsNone(self.w.srv.task)

    def test_get_missing_and_folder(self):
        self.assertEqual(self.w.last(self.w.req("get", path="/x"))["code"], "missing")
        self.assertEqual(self.w.last(self.w.req("get", path="/documents"))["code"], "isdir")

    def test_read_head(self):
        self.file("/avatar.bin", b"OAV1\x48\x48\x07octocat" + b"\x00" * 50)
        m = self.w.last(self.w.req("read", path="/avatar.bin", n=14))
        self.assertEqual(base64.b64decode(m["data"]), b"OAV1\x48\x48\x07octocat")
        self.assertEqual(self.w.last(self.w.req("read", path="/none", n=4))["missing"], 1)
        self.assertEqual(self.w.last(self.w.req("read", path="/avatar.bin", n=10 ** 6))["code"],
                         "size")

    def test_sha256_and_rm_run_as_jobs(self):
        self.file("/documents/d/e/f.txt", b"hi")
        rid = self.w.req("sha256", path="/documents/d/e/f.txt")
        self.w.run()
        self.assertEqual(self.w.last(rid)["sha"], hashlib.sha256(b"hi").hexdigest())
        self.assertEqual(self.w.last(self.w.req("rm", path="/documents/d"))["code"], "isdir")
        rid = self.w.req("rm", path="/documents/d", recursive=True)
        self.w.run()
        self.assertEqual(self.w.last(rid)["op"], "ok")
        self.assertFalse(os.path.exists(self.tmp + "/documents/d"))
        self.assertEqual(self.w.last(self.w.req("rm", path="/documents/d"))["code"], "missing")

    def test_threaded_job(self):
        w = Wire(self.tmp, threaded=True)
        w.hello()
        self.file("/documents/t.txt", b"x" * 5000)
        rid = w.req("sha256", path="/documents/t.txt")
        import time
        for _ in range(200):
            w.srv.tick()
            if w.srv.task is None:
                break
            time.sleep(0.005)
        self.assertEqual(w.last(rid)["sha"], hashlib.sha256(b"x" * 5000).hexdigest())


class ScreenTest(_Case):
    def display(self, buf):
        d = types.SimpleNamespace(_buf=buf, _dirty=False)
        mod = types.ModuleType("oreoWare.display")
        mod.current = lambda: d
        pkg = types.ModuleType("oreoWare")
        pkg.display = mod
        return {"oreoWare": pkg, "oreoWare.display": mod}

    def test_screen_is_a_copy_of_the_presented_frame(self):
        frame = bytearray(os.urandom(320 * 240 * 2))
        want = bytes(frame)
        with mock.patch.dict(sys.modules, self.display(frame)):
            rid = self.w.req("screen")
        head = self.w.last(rid)
        self.assertEqual((head["w"], head["h"], head["n"], head["live"]),
                         (320, 240, len(want), False))
        frame[:100] = b"\xff" * 100              # the next frame is drawn meanwhile
        self.w.run(500)
        got = b"".join(base64.b64decode(c.split(" ", 2)[2]) for c in self.w.chunks())
        self.assertEqual(got, want)
        self.assertEqual(self.w.last(rid)["sha"], hashlib.sha256(want).hexdigest())

    def test_no_display_yet(self):
        mods = self.display(None)
        with mock.patch.dict(sys.modules, mods):
            self.assertEqual(self.w.last(self.w.req("screen"))["code"], "none")


class FakeWifi(types.ModuleType):
    CONN_BUSY, CONN_OK = "connecting", "ok"

    def __init__(self):
        super().__init__("oreoWare.wifi")
        self.saved = []
        self.up = False
        self.polls = 0
        self.stopped = 0
        self.state = "off"

    def auto_stop(self):
        self.stopped += 1

    def scan_seq(self):
        return 0

    def scan_start(self):
        self.polls = 0
        return True

    def scan_poll(self):
        self.polls += 1
        return "busy" if self.polls < 3 else "done"

    def scan_error(self):
        return None

    def nearby(self):
        return [{"ssid": "Home", "rssi": -50, "secure": True, "ch": 6}]

    def saved_ssids(self):
        return {n["ssid"]: 1 for n in self.saved}

    def connect_begin(self, ssid, pw, timeout):
        self.target = (ssid, pw)
        self.polls = 0
        return True

    def connect_poll(self):
        self.polls += 1
        if self.polls < 3:
            return self.CONN_BUSY
        ok = self.target == ("Home", "hunter2!")
        self.up = ok
        return self.CONN_OK if ok else "bad_password"

    def add_saved(self, ssid, pw, priority=10):
        self.saved.append({"ssid": ssid, "password": pw, "priority": priority})
        return True

    def _mark_last(self, ssid):
        pass

    def is_connected(self):
        return self.up

    def ip(self):
        return "10.0.0.2" if self.up else None

    def ssid(self):
        return "Home"

    def rssi(self):
        return -50

    def list_saved(self):
        return list(self.saved)

    def remove_saved(self, ssid):
        n = len(self.saved)
        self.saved = [x for x in self.saved if x["ssid"] != ssid]
        return n != len(self.saved)

    def _secrets_networks(self):
        return []

    def auto_begin(self):
        self.state = "connecting"
        self.begun = True

    def auto_tick(self):
        if self.saved:
            self.up = True
        else:
            self.state = "no_network"

    def auto_state(self):
        return self.state


class WifiTest(_Case):
    def setUp(self):
        super().setUp()
        self.wifi = FakeWifi()
        pkg = types.ModuleType("oreoWare")
        pkg.wifi = self.wifi
        p = mock.patch.dict(sys.modules, {"oreoWare": pkg, "oreoWare.wifi": self.wifi})
        p.start()
        self.addCleanup(p.stop)

    def test_scan_is_polled_across_frames(self):
        rid = self.w.req("wifi_scan")
        self.assertIsNotNone(self.w.srv.task)
        self.assertEqual(self.w.srv.overlay()[0], "Wi-Fi scan")
        self.w.run()
        self.assertEqual(self.w.last(rid)["nets"][0]["ssid"], "Home")
        self.assertGreaterEqual(self.wifi.polls, 3)

    def test_connect_saves_and_never_echoes_the_password(self):
        rid = self.w.req("wifi_connect", ssid="Home", password="hunter2!", save=True)
        self.w.run()
        m = self.w.last(rid)
        self.assertEqual((m["result"], m["ip"], m["saved"]), ("ok", "10.0.0.2", True))
        self.assertTrue(self.wifi.stopped)
        self.assertNotIn("hunter2!", "\n".join(self.w.out))
        rid = self.w.req("wifi_connect", ssid="Home", password="wrong-pass")
        self.w.run()
        self.assertEqual(self.w.last(rid)["result"], "bad_password")

    def test_saved_status_forget_online(self):
        self.wifi.add_saved("Home", "hunter2!")
        saved = self.w.last(self.w.req("wifi_saved"))["saved"]
        self.assertEqual(saved[0]["ssid"], "Home")
        self.assertNotIn("password", saved[0])
        rid = self.w.req("wifi_online")
        self.w.run()
        self.assertTrue(self.w.last(rid)["online"])
        self.assertTrue(self.w.last(self.w.req("wifi_status"))["wifi"]["up"])
        self.assertTrue(self.w.last(self.w.req("wifi_forget", ssid="Home"))["removed"])

    def test_online_gives_up_without_saved_networks(self):
        rid = self.w.req("wifi_online")
        self.w.clock.ms += 2000
        self.w.run()
        self.assertFalse(self.w.last(rid)["online"])


class AppsAndProfileTest(_Case):
    def setUp(self):
        super().setUp()
        store = types.ModuleType("oreoOS.store")
        store.HIDDEN_INSTALLED = ("launcher",)
        store.installed_manifest = lambda n: {"name": n.title()}
        store.installed_version = lambda n: "1.0"
        store.is_default = lambda n: n == "settings"
        store.catalogue = lambda: ([{"id": "snake", "name": "Snake", "version": "1.1",
                                     "size": 3000, "tagline": "Eat", "category": "Games"}], [])
        store.app_state = lambda e: "install"
        self.installed = []

        def install(app_id, cb):
            for done in (1000, 3000):
                cb(done, 3000, "Downloading")
            self.installed.append(app_id)
            return True, "installed"
        store.install = install
        store.uninstall = lambda a: (a != "settings", "removed")
        store.refresh = lambda: True
        store.last_error = lambda: ""
        store.forget_installed = mock.Mock()
        store._invalidate_launcher = mock.Mock()
        self.store = store
        self.profile = types.ModuleType("oreoOS.profile")
        self.profile.PATH = "/profile.json"
        self.profile.reload = mock.Mock()
        self.netsvc = types.ModuleType("oreoOS.netsvc")
        self.netsvc.request_sync = mock.Mock(return_value=True)
        pkg = types.ModuleType("oreoOS")
        pkg.store, pkg.profile, pkg.netsvc = store, self.profile, self.netsvc
        p = mock.patch.dict(sys.modules, {"oreoOS": pkg, "oreoOS.store": store,
                                          "oreoOS.profile": self.profile,
                                          "oreoOS.netsvc": self.netsvc})
        p.start()
        self.addCleanup(p.stop)
        os.makedirs(self.tmp + "/apps/settings")
        os.makedirs(self.tmp + "/apps/launcher")

    def test_apps_list(self):
        rid = self.w.req("apps")
        self.w.run()
        m = self.w.last(rid)
        self.assertEqual([a["id"] for a in m["installed"]], ["settings"])
        self.assertEqual(m["catalogue"][0]["state"], "install")

    def test_install_reports_progress_and_shows_a_strip(self):
        rid = self.w.req("app_install", app="snake", title="Installing app", detail="Snake")
        self.assertEqual(self.w.srv.overlay()[:2], ("Installing app", "Snake"))
        self.w.run()
        prog = [m for m in self.w.msgs(rid) if m["op"] == "progress"]
        self.assertEqual(prog[-1]["done"], 3000)
        self.assertEqual(self.w.last(rid), {"op": "app_install", "id": rid, "ok": True,
                                            "msg": "installed"})
        self.assertEqual(self.installed, ["snake"])
        self.assertIsNone(self.w.srv.overlay())

    def test_remove_refresh_and_apps_changed(self):
        rid = self.w.req("app_remove", app="settings")
        self.w.run()
        self.assertFalse(self.w.last(rid)["ok"])
        rid = self.w.req("apps_refresh")
        self.w.run()
        self.assertTrue(self.w.last(rid)["ok"])
        self.w.req("apps_changed")
        self.store.forget_installed.assert_called_once_with()
        self.store._invalidate_launcher.assert_called_once_with()

    def test_profile_write_merges_reloads_and_resyncs_on_a_new_timezone(self):
        self.file("/profile.json", json.dumps({"GITHUB_USER": "octocat",
                                               "OWM_API_KEY": "k3y", "X": 1}).encode())
        m = self.w.last(self.w.req("profile_write",
                                   updates={"DISPLAY_NAME": "Mona", "GITHUB_USER": "",
                                            "TIMEZONE_OFFSET": 5.5}))
        self.assertEqual(m["keys"], ["DISPLAY_NAME", "OWM_API_KEY", "TIMEZONE_OFFSET", "X"])
        self.assertTrue(m["reloaded"])
        self.assertTrue(m["time_sync"])
        self.profile.reload.assert_called_once_with()
        self.netsvc.request_sync.assert_called_once_with()
        saved = json.loads(self.read("/profile.json"))
        self.assertEqual(saved, {"OWM_API_KEY": "k3y", "X": 1, "DISPLAY_NAME": "Mona",
                                 "TIMEZONE_OFFSET": 5.5})
        self.assertFalse(os.path.exists(self.tmp + "/profile.json.tmp"))
        r = self.w.last(self.w.req("profile_read", keys=["DISPLAY_NAME", "OWM_API_KEY"],
                                   secret="OWM_API_KEY"))
        self.assertEqual(r["file"], {"DISPLAY_NAME": "Mona", "OWM_API_KEY": True})
        self.assertEqual(r["state"], "ok")
        self.assertNotIn("k3y", "\n".join(self.w.out))

    def test_same_timezone_needs_no_resync(self):
        self.w.req("profile_write", updates={"DISPLAY_NAME": "Mona"})
        self.netsvc.request_sync.assert_not_called()


class ReaderTest(unittest.TestCase):
    class Stream:
        def __init__(self, data):
            self.data = bytearray(data)

        def readinto(self, buf):
            if not self.data:
                return 0
            buf[0] = self.data.pop(0)
            return 1

    class Poller:
        def __init__(self, stream):
            self.stream = stream

        def ipoll(self, timeout):
            return [1] if self.stream.data else []

    def reader(self, data, max_line=L.MAX_LINE):
        s = self.Stream(data)
        return L.Reader(max_line, stream=s, poller=self.Poller(s)), s

    def test_lines_and_partial_lines(self):
        r, s = self.reader(b"one\r\ntwo\nthr")
        self.assertEqual(r.read_lines(), [b"one", b"two"])
        s.data += b"ee\n"
        self.assertEqual(r.read_lines(), [b"three"])
        self.assertEqual(r.read_lines(), [])        # nothing waiting: returns at once

    def test_over_long_line_is_reported_once(self):
        r, _ = self.reader(b"x" * 50 + b"\nok\n", max_line=10)
        self.assertEqual(r.read_lines(), [None, b"ok"])

    def test_stops_after_max_lines(self):
        r, s = self.reader(b"a\n" * 20)
        self.assertEqual(len(r.read_lines(max_lines=8)), 8)
        self.assertEqual(len(s.data), 24)

    def test_never_touches_ctrl_c(self):
        mp = types.ModuleType("micropython")
        mp.kbd_intr = mock.Mock()
        with mock.patch.dict(sys.modules, {"micropython": mp}):
            mod = load("oreoOS/livelink.py", "livelink_kbd_check")
            r, _ = self.reader(b"#@OLH {}\n")
            r.read_lines()
            mod.tick  # loaded and used without switching Ctrl-C off
        mp.kbd_intr.assert_not_called()
        self.assertNotIn("import micropython", (ROOT / "oreoOS/livelink.py").read_text())


class RunLoopGlueTest(unittest.TestCase):
    def setUp(self):
        self.mod = load("oreoOS/livelink.py", "livelink_glue")
        self.out = []
        self.mod._server = self.mod.Server(self.out.append, threaded=False)
        stream = ReaderTest.Stream(b"")
        self.stream = stream
        self.mod._reader = self.mod.Reader(stream=stream, poller=ReaderTest.Poller(stream))

    def test_tick_answers_and_reports_activity(self):
        self.stream.data += self.mod.HOST + b'{"op":"hello","v":1,"id":7}\n'
        self.assertTrue(self.mod.tick(types.SimpleNamespace(), False))
        self.assertIn('"id": 7', self.out[0])

    def test_steps_aside_for_an_app_that_owns_the_console(self):
        transfer = types.SimpleNamespace(OWNS_USB_CONSOLE=True)
        self.stream.data += self.mod.HOST + b'{"op":"hello","v":1,"id":7}\n'
        self.assertFalse(self.mod.tick(transfer, False))
        self.assertEqual(self.out, [])
        self.assertTrue(self.stream.data)            # left for the app to read
        self.assertFalse(self.mod.pending())
        self.stream.data = bytearray()
        self.mod.tick(types.SimpleNamespace(), False)
        self.stream.data += self.mod.HOST + b'{"op":"hello","v":1,"id":8}\n'
        self.assertTrue(self.mod.tick(types.SimpleNamespace(), False))
        self.assertIn('"id": 8', self.out[-1])

    def test_pending_wakes_power(self):
        self.assertFalse(self.mod.pending())
        self.stream.data += b"#"
        self.assertTrue(self.mod.pending())

    def test_draw_only_while_a_titled_task_runs(self):
        calls = []
        d = types.SimpleNamespace(rect=lambda *a, **k: calls.append(("rect", a)),
                                  text=lambda *a, **k: calls.append(("text", a)))
        api = types.ModuleType("oreoOS.api")
        api.SCREEN_W, api.SCREEN_H, api.WHITE = 320, 240, 0xFFFF
        theme = types.ModuleType("oreoOS.theme")
        theme.TEXT_BRIGHT, theme.MUTED, theme.PRIMARY = 1, 2, 3
        pkg = types.ModuleType("oreoOS")
        pkg.api, pkg.theme = api, theme
        with mock.patch.dict(sys.modules, {"oreoOS": pkg, "oreoOS.api": api,
                                           "oreoOS.theme": theme}):
            self.assertFalse(self.mod.draw(d))
            srv = self.mod._server
            srv.task = types.SimpleNamespace(title="Receiving files", detail="a.txt",
                                             progress=lambda: 0.5, receiving=False)
            self.assertTrue(self.mod.draw(d))
        texts = [a[0] for k, a in calls if k == "text"]
        self.assertTrue(texts[0].startswith("USB  Receiving files: a.txt"))
        self.assertTrue(texts[0].endswith("50%"))


class WiringTest(unittest.TestCase):
    def test_shipped_and_deployed(self):
        self.assertIn('"oreoOS/livelink.py"', (ROOT / "tools/deploy.py").read_text())
        self.assertIn('"oreoOS/livelink.py"', (ROOT / "tools/build_release.py").read_text())

    def test_run_loop_power_and_transfer_use_it(self):
        launcher = (ROOT / "oreoOS/launcher.py").read_text()
        self.assertIn("from oreoOS import livelink as _live", launcher)
        self.assertIn("_live.tick(app, realtime)", launcher)
        self.assertIn("_live.draw(os_obj.display)", launcher)
        self.assertIn("livelink.pending", (ROOT / "oreoOS/power.py").read_text())
        self.assertIn("OWNS_USB_CONSOLE = True",
                      (ROOT / "apps/transfer/src/app.py").read_text())


if __name__ == "__main__":
    unittest.main()
