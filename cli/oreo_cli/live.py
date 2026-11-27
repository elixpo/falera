# SPDX-License-Identifier: MIT
"""The live link: commands answered by a running FaleraOS.

FaleraOS (oreoOS/livelink.py, from the release after v2.1) reads framed
requests from the USB console once per frame and answers them without
stopping, so `info`, `ls`, `push`, `pull`, `rm`, `screenshot`, `apps`,
`wifi`, `setup`, `add` and `app install` leave the badge on screen. This
module is the computer's end; cli/tests/test_live.py runs it against the
real oreoOS/livelink.py.

  computer -> badge   #@OLH {json}             request: op, id, fields
                      #@OLD <seq> <base64>     a chunk of a file being put
  badge -> computer   @OLB {json}              reply, progress or error
                      @OLD <seq> <base64>      a chunk of a file or the screen

Every line the computer sends starts with '#': if it reaches the REPL
instead of the live link (an older FaleraOS, FaleraOS stopped), it is a
Python comment there and does nothing. The first request is a hello; an
older FaleraOS does not answer, and `connect()` returns None after
HELLO_TRIES short waits so the caller falls back to the raw REPL. The few
bytes of those hellos sit in MicroPython's 259-byte stdin ring until the
REPL reads them as comments, leaving room for the Ctrl-C that follows.

`LiveBadge` has the same methods as badge.Badge for the commands above,
so they run unchanged on either. Nothing is restarted at the end.
"""

import base64
import binascii
import contextlib
import hashlib
import json
import posixpath
import time

from . import __version__
from .badge import FREE_RESERVE, norm
from .errors import BadgeError, LinkError, OreoError

PROTOCOL = 1
HOST = b"#@OLH "
HOST_DATA = b"#@OLD "
BADGE = b"@OLB "
BADGE_DATA = b"@OLD "

HELLO_WAIT_S = 1.0           # per hello; FaleraOS reads the console every frame
HELLO_TRIES = 2
WINDOW = 2                   # data lines in flight before waiting for an ack
REQUEST_S = 15.0             # silence allowed while waiting for a reply
JOB_S = 120.0                # ... for work on the badge's worker (no progress events)

TRANSFER_OPEN_HINT = ("press HOME (or B) on the badge to leave Transfer, then run the "
                      "command again")

# Commands that need the raw REPL keep it: update/OTA install, flash, dev,
# run, shell, logs --restart, doctor, the full-screen view.


class LiveError(OreoError):
    """The badge answered a request with an error."""

    def __init__(self, message, code="", hint=None, reply=None):
        super().__init__(message, hint=hint)
        self.badge_code = code
        self.reply = reply or {}


class TransferOpen(LinkError):
    pass


class Client:
    """The computer's end of the live link on an open serial port
    (pyserial, or the test fake: write(), read(n), in_waiting)."""

    def __init__(self, ser, clock=time.monotonic, sleep=time.sleep, check=None):
        self.ser = ser
        self.clock = clock
        self.sleep = sleep
        self.check = check
        self._rx = bytearray()
        self._queue = []
        self._id = 0
        self.badge = {}
        self.noise = 0
        self.saw_transfer = False

    # ── wire ────────────────────────────────────────────────────────
    def _write(self, data):
        try:
            self.ser.write(data)
            flush = getattr(self.ser, "flush", None)
            if flush is not None:
                flush()
        except OSError:
            raise LinkError("lost the connection to the badge",
                            hint="plug it back in, check with `falera devs`, and try again")
        except Exception as e:
            if "Timeout" not in e.__class__.__name__:
                raise
            raise LinkError("the badge stopped reading the USB port",
                            hint="FaleraOS may be busy; try again, or use --no-live")

    def send(self, op, prefix=b"", **fields):
        self._id += 1
        fields["op"] = op
        fields["id"] = self._id
        self._write(prefix + HOST + json.dumps(fields, separators=(",", ":")).encode() + b"\n")
        return self._id

    def send_chunk(self, seq, piece):
        self._write(HOST_DATA + str(seq).encode() + b" " + base64.b64encode(piece) + b"\n")

    def _waiting(self):
        n = getattr(self.ser, "in_waiting", None)
        if n is None:
            n = self.ser.inWaiting()
        return n

    @staticmethod
    def _find(line, prefix):
        j = line.find(prefix)
        while j > 0 and line[j - 1:j] == b"#":     # our own line echoed by a REPL
            j = line.find(prefix, j + 1)
        return j

    def _parse(self):
        while True:
            i = self._rx.find(b"\n")
            if i < 0:
                return
            line = bytes(self._rx[:i]).rstrip(b"\r")
            del self._rx[:i + 1]
            j = self._find(line, BADGE)
            if j >= 0:
                try:
                    msg = json.loads(line[j + len(BADGE):].decode("utf-8"))
                except ValueError:
                    self.noise += 1
                    continue
                if isinstance(msg, dict) and isinstance(msg.get("op"), str):
                    self._queue.append(msg)
                continue
            j = self._find(line, BADGE_DATA)
            if j >= 0:
                rest = line[j + len(BADGE_DATA):]
                try:
                    sp = rest.index(b" ")
                    self._queue.append({"op": "_data", "seq": int(rest[:sp]),
                                        "data": binascii.a2b_base64(rest[sp + 1:])})
                except (ValueError, binascii.Error):
                    self._queue.append({"op": "_bad"})
                continue
            if b"@OTB " in line:
                self.saw_transfer = True
            if line.strip():
                self.noise += 1

    def recv(self, timeout):
        """The next message, or None after `timeout` seconds of silence."""
        deadline = self.clock() + timeout
        while True:
            if self._queue:
                return self._queue.pop(0)
            if self.check is not None:
                self.check()
            try:
                n = self._waiting()
                if n:
                    self._rx += self.ser.read(n)
                    self._parse()
                    continue
            except OSError:
                raise LinkError("lost the connection to the badge",
                                hint="plug it back in, check with `falera devs`, and try again")
            if self.clock() >= deadline:
                return None
            data = self.ser.read(1)
            if data:
                self._rx += data
                self._parse()
            else:
                self.sleep(0.002)

    def wait(self, rid, timeout=REQUEST_S, on_progress=None, on_data=None):
        """The reply to request `rid`; progress events and data lines go
        to the callbacks and restart the timeout. Errors raise LiveError."""
        while True:
            msg = self.recv(timeout)
            if msg is None:
                raise LinkError("FaleraOS stopped answering", hint="try again, or use --no-live")
            op = msg["op"]
            if op == "_data":
                if on_data is None:
                    continue
                on_data(msg["seq"], msg["data"])
                continue
            if op == "_bad":
                if on_data is not None:
                    on_data(None, None)
                continue
            if msg.get("id") not in (rid, None):
                continue                # a late reply to something we gave up on
            if op == "progress":
                if on_progress is not None:
                    on_progress(msg)
                continue
            if op == "error":
                if msg.get("id") is None:
                    self.noise += 1     # about a stray line, not this request
                    continue
                raise LiveError(msg.get("msg") or msg.get("code") or "error",
                                code=msg.get("code", ""), reply=msg)
            return msg

    def call(self, op, timeout=REQUEST_S, on_progress=None, **fields):
        return self.wait(self.send(op, **fields), timeout, on_progress)

    # ── session ─────────────────────────────────────────────────────
    def hello(self, wait=HELLO_WAIT_S, tries=HELLO_TRIES):
        """The badge's hello reply, or None when nothing answered (an
        older FaleraOS, FaleraOS stopped, the Transfer app open)."""
        for attempt in range(tries):
            # The leading newline ends any half line left in the badge's
            # buffer. Kept short: on an older FaleraOS both hellos wait in
            # the 259-byte stdin ring until the REPL reads them.
            rid = self.send("hello", prefix=b"\n", v=PROTOCOL, cli=__version__)
            deadline = self.clock() + wait
            while True:
                left = deadline - self.clock()
                if left <= 0:
                    break
                msg = self.recv(left)
                if msg is None:
                    break
                if msg.get("op") == "hello" and msg.get("id") == rid:
                    self.badge = msg
                    return msg
                if msg.get("op") == "error" and msg.get("id") == rid:
                    if msg.get("code") == "version":
                        self.badge = {"version_mismatch": msg.get("v")}
                        return None
                    if msg.get("code") == "busy":
                        continue
            if self.saw_transfer:
                return None
        return None

    def bye(self):
        try:
            rid = self.send("bye")
            deadline = self.clock() + 1.0
            while self.clock() < deadline:
                msg = self.recv(max(0.0, deadline - self.clock()))
                if msg is None or (msg.get("op") == "bye" and msg.get("id") == rid):
                    break
        except Exception:
            pass

    def abort(self):
        try:
            rid = self.send("abort")
            deadline = self.clock() + 2.0
            while self.clock() < deadline:
                msg = self.recv(max(0.0, deadline - self.clock()))
                if msg is None or (msg.get("op") == "aborted" and msg.get("id") == rid):
                    break
        except Exception:
            pass


def connect(link, ui=None, clock=time.monotonic, sleep=time.sleep):
    """A LiveBadge when FaleraOS on the other end of `link` answers the
    live link's hello, else None (the caller uses the raw REPL). Raises
    TransferOpen when the Transfer app holds the console: Ctrl-C is off
    then, and the raw REPL would not get in either."""
    raw = getattr(link, "raw_serial", None)
    if raw is None:
        return None
    ser = raw()
    c = Client(ser, clock=clock, sleep=sleep)
    hello = c.hello()
    if hello is None:
        if c.saw_transfer:
            raise TransferOpen("the Transfer app is open on the badge and holds the USB "
                               "console", hint=TRANSFER_OPEN_HINT)
        if ui is not None and c.badge.get("version_mismatch") is not None:
            ui.note("FaleraOS speaks live link v%s, this falera-cli v%d: using the raw REPL "
                    "(update both to match)" % (c.badge["version_mismatch"], PROTOCOL))
        return None
    return LiveBadge(link, c, hello)


def _not_found(path):
    return OreoError("%s: not found on the badge" % path)


class LiveBadge:
    """badge.Badge's interface over the live link. FaleraOS keeps running."""

    live = True

    def __init__(self, link, client, hello, on_text=None):
        self.link = link
        self.c = client
        self.hello = hello
        self.on_text = on_text
        self.entered = True
        self.port = None
        self._dirs = set()
        self.os_version = hello.get("os")
        self.ops = set(hello.get("ops") or ())

    def supports(self, *ops):
        return all(op in self.ops for op in ops)

    # ── session ─────────────────────────────────────────────────────
    def connect(self):
        return self

    def finish(self, mode="soft"):
        """Say goodbye and close the port; FaleraOS just keeps running."""
        if self.link is None:
            return
        try:
            self.c.bye()
        finally:
            try:
                self.link.close()
            except Exception:
                pass
            self.link = None
            self.entered = False

    # The badge draws its own progress strip for titled requests; the
    # raw REPL path's hostui calls have nothing to do here.
    def screen(self, title, detail="", progress=None):
        pass

    def screen_clear(self):
        pass

    @contextlib.contextmanager
    def showing(self):
        yield self

    def _call(self, op, timeout=REQUEST_S, on_progress=None, **fields):
        try:
            return self.c.call(op, timeout=timeout, on_progress=on_progress, **fields)
        except KeyboardInterrupt:
            self.c.abort()
            raise

    # ── device info ─────────────────────────────────────────────────
    def info(self):
        return self._call("info", timeout=JOB_S)["info"]

    def banner(self):
        info = self.info()
        return {"impl": "micropython", "mpy": info.get("mpy"), "machine": info.get("machine"),
                "fw": info.get("fw"), "os": info.get("os")}

    # ── files ───────────────────────────────────────────────────────
    def stat(self, path):
        r = self._call("stat", path=norm(path))
        if r.get("missing"):
            return None
        return {"dir": bool(r.get("dir")), "size": r.get("size", 0)}

    def ls(self, path="/"):
        p = norm(path)
        try:
            r = self._call("ls", path=p)
        except LiveError as e:
            if e.badge_code == "missing":
                raise _not_found(p)
            raise
        rows = [r["file"]] if "file" in r else r["ls"]
        out = [{"name": n, "dir": bool(d), "size": s} for n, d, s in rows]
        out.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return out

    def walk(self, path):
        p = norm(path)
        for e in self.ls(p):
            child = posixpath.join(p, e["name"])
            if e["dir"]:
                for item in self.walk(child):
                    yield item
            else:
                yield child, e["size"]

    def free(self):
        r = self._call("free")
        return r["free"], r["total"]

    def mkdirs(self, path):
        p = norm(path)
        if p == "/" or p in self._dirs:
            return
        self._call("mkdirs", path=p)
        while p != "/":
            self._dirs.add(p)
            p = posixpath.dirname(p)

    def write_file(self, data, remote, progress=None, verify=True, chunk=None,
                   reserve=FREE_RESERVE, check_space=True, screen=None):
        """Copy bytes to `remote`: written to <remote>.part on the badge,
        checked (size, SHA-256 unless verify=False) and renamed into
        place. The badge shows a progress strip when `screen` is given."""
        dest = norm(remote)
        if check_space:
            free, _ = self.free()
            if len(data) + reserve > free:
                from .ui import human
                raise OreoError("not enough space on the badge: %s needed, %s free"
                                % (human(len(data)), human(max(0, free - reserve))),
                                hint="free some space (`falera info`, `falera rm`)")
        parent = posixpath.dirname(dest)
        if parent and parent != "/":
            self.mkdirs(parent)
        fields = {"path": dest, "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest() if verify else None,
                  "check_space": False}
        if screen:
            fields["title"], fields["detail"] = str(screen[0]), str(screen[1] or "")
        c = self.c
        rid = c.send("put", **fields)
        try:
            r = c.wait(rid)
            if r["op"] != "ready":
                raise BadgeError("unexpected answer to put: %s" % r["op"])
            size = min(int(r.get("chunk") or 1536), 1536)
            count = (len(data) + size - 1) // size
            sent, acked = 0, -1
            msg = None
            while msg is None:
                while sent < count and sent - acked - 1 < WINDOW:
                    c.send_chunk(sent, data[sent * size:(sent + 1) * size])
                    sent += 1
                m = c.wait(rid)
                if m["op"] == "ack":
                    acked = int(m.get("seq", acked))
                    if progress:
                        progress(int(m.get("got", 0)), len(data))
                elif m["op"] == "done":
                    msg = m
                else:
                    raise BadgeError("unexpected answer while sending: %s" % m["op"])
        except LiveError as e:
            raise OreoError("%s: %s; nothing was replaced" % (dest, e.message),
                            hint="try again; if it repeats, try another cable or USB port"
                            if e.badge_code in ("hash", "size", "data", "seq") else
                            "free some space (`falera info`, `falera rm`)"
                            if e.badge_code == "no_space" else None)
        except KeyboardInterrupt:
            c.abort()
            raise
        if msg["op"] != "done":
            raise BadgeError("unexpected answer: %s" % msg["op"])
        if msg.get("size") != len(data):
            raise OreoError("%s: the badge received %s bytes, expected %d; nothing was replaced"
                            % (dest, msg.get("size"), len(data)))
        if progress and not data:
            progress(0, 0)
        return dest

    def sha256(self, remote):
        return self._call("sha256", timeout=JOB_S, path=norm(remote)).get("sha")

    def _stream(self, op, fields, total_key, progress=None):
        """(header, bytes) of a get/screen stream, checked against the
        count and SHA-256 the badge sends at the end."""
        c = self.c
        rid = c.send(op, **fields)
        buf = bytearray()
        state = {"seq": 0, "bad": None, "total": None}

        def on_data(seq, piece):
            if seq is None or seq != state["seq"]:
                state["bad"] = state["bad"] or ("damaged line" if seq is None else
                                                "chunk %s arrived, %d expected" % (seq, state["seq"]))
                return
            state["seq"] += 1
            buf.extend(piece)
            if progress:
                progress(len(buf), state["total"])
        try:
            head = c.wait(rid)
            state["total"] = head.get(total_key)
            end = c.wait(rid, on_data=on_data)
        except KeyboardInterrupt:
            c.abort()
            raise
        if end["op"] != "eof":
            raise BadgeError("unexpected answer: %s" % end["op"])
        why = state["bad"]
        if not why and len(buf) != end.get("n"):
            why = "received %d of %s bytes" % (len(buf), end.get("n"))
        if not why and end.get("sha") and hashlib.sha256(bytes(buf)).hexdigest() != end["sha"]:
            why = "the SHA-256 check failed"
        return head, bytes(buf), why

    def read_file(self, remote, progress=None, chunk=None, screen=None):
        src = norm(remote)
        fields = {"path": src}
        if screen:
            fields["title"], fields["detail"] = str(screen[0]), str(screen[1] or "")
        last = None
        for _ in range(2):
            try:
                head, data, why = self._stream("get", fields, "size", progress)
            except LiveError as e:
                if e.badge_code == "missing":
                    raise _not_found(src)
                if e.badge_code == "isdir":
                    raise OreoError("%s is a directory" % src, hint="use `falera pull -r`")
                raise
            if not why:
                if len(data) != head.get("size"):
                    why = "received %d of %s bytes" % (len(data), head.get("size"))
                else:
                    return data
            last = why
        raise OreoError("%s: %s" % (src, last), hint="try again; if it repeats, try "
                        "another cable or USB port")

    def screenshot(self, attempts=2):
        """(width, height, RGB565 bytes, dirty) of the frame on the panel."""
        last = None
        for _ in range(max(1, attempts)):
            try:
                head, data, why = self._stream("screen", {}, "n")
            except LiveError as e:
                if e.badge_code == "none":
                    raise OreoError("FaleraOS has not drawn anything yet (no display)",
                                    hint="wait until the home screen shows, then try again")
                raise
            w, h, n = int(head["w"]), int(head["h"]), int(head["n"])
            if n != w * h * 2:
                raise OreoError("the badge's framebuffer is %d bytes, not %dx%d RGB565"
                                % (n, w, h))
            if not why:
                return w, h, data, bool(head.get("live"))
            last = why
        raise OreoError("the screen did not arrive intact (%s)" % last,
                        hint="try again; `falera -v screenshot` shows more")

    def read_head(self, path, n):
        """The first n bytes of a file, or None when it is missing."""
        r = self._call("read", path=norm(path), n=int(n))
        if r.get("missing"):
            return None
        return base64.b64decode(r.get("data") or "")

    def remove_quiet(self, path):
        try:
            self._call("rm", timeout=JOB_S, path=norm(path))
        except LiveError as e:
            if e.badge_code != "missing":
                raise

    def rm(self, path, recursive=False):
        p = norm(path)
        try:
            self._call("rm", timeout=JOB_S, path=p, recursive=bool(recursive))
        except LiveError as e:
            if e.badge_code == "missing":
                raise _not_found(p)
            if e.badge_code == "isdir":
                raise OreoError("%s is a directory" % p, hint="use `falera rm -r`")
            raise OreoError("could not remove %s (%s)" % (p, e.message))
        return p

    # ── wifi ────────────────────────────────────────────────────────
    def wifi_status(self):
        return self._call("wifi_status").get("wifi")

    def wifi_scan(self, timeout_s=15):
        try:
            r = self._call("wifi_scan", timeout=timeout_s + 10, timeout_ms=int(timeout_s * 1000))
        except LiveError as e:
            raise OreoError("Wi-Fi scan failed on the badge: %s" % e.message,
                            hint="try again; see issue #120 if it keeps failing")
        saved = set(r.get("saved") or ())
        for n in r["nets"]:
            n["saved"] = n["ssid"] in saved
        return r["nets"]

    def wifi_connect(self, ssid, password, save=True, timeout_ms=15000):
        r = self._call("wifi_connect", timeout=timeout_ms / 1000.0 + 15, ssid=ssid,
                       password=password or "", save=bool(save), timeout_ms=int(timeout_ms))
        return {"result": r.get("result"), "ip": r.get("ip"), "saved": r.get("saved")}

    def wifi_save(self, ssid, password, priority=10):
        return bool(self._call("wifi_save", ssid=ssid, password=password or "",
                               priority=int(priority)).get("saved"))

    def wifi_saved(self):
        return self._call("wifi_saved")["saved"]

    def wifi_forget(self, ssid):
        r = self._call("wifi_forget", ssid=ssid)
        return bool(r.get("removed")), bool(r.get("deploy"))

    def wifi_online(self, timeout_s=90):
        r = self._call("wifi_online", timeout=timeout_s + 10, timeout_ms=int(timeout_s * 1000))
        return {"online": bool(r.get("online")), "ip": r.get("ip")}

    # ── apps ────────────────────────────────────────────────────────
    def apps(self):
        r = self._call("apps", timeout=JOB_S)
        return {"installed": r.get("installed") or [], "catalogue": r.get("catalogue") or []}

    def apps_refresh(self):
        r = self._call("apps_refresh", timeout=JOB_S)
        return bool(r.get("ok")), r.get("error") or ""

    def app_install(self, app_id, progress=None, screen=None):
        def on_progress(ev):
            if progress:
                progress(ev.get("done", 0), ev.get("total") or 0)
        fields = {"app": app_id}
        if screen:
            fields["title"], fields["detail"] = str(screen[0]), str(screen[1] or "")
        r = self._call("app_install", timeout=JOB_S, on_progress=on_progress, **fields)
        return bool(r.get("ok")), r.get("msg") or ""

    def app_remove(self, app_id):
        r = self._call("app_remove", timeout=JOB_S, app=app_id)
        return bool(r.get("ok")), r.get("msg") or ""

    def apps_changed(self):
        self._call("apps_changed")

    # ── owner profile ───────────────────────────────────────────────
    def profile_read(self, keys, secret):
        r = self._call("profile_read", keys=list(keys), secret=secret)
        return {"file": r.get("file"), "state": r.get("state"), "secrets": r.get("secrets") or {}}

    def profile_write(self, updates):
        try:
            return self._call("profile_write", updates=updates)
        except LiveError as e:
            raise BadgeError(e.message)
