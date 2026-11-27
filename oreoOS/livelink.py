# SPDX-License-Identifier: MIT
"""Live link: `falera` commands answered while FaleraOS keeps running.

The badge's native USB port is one serial stream shared by the REPL,
print() and the FaleraOS log. Most `falera` commands used to stop FaleraOS
with Ctrl-C, work through the raw REPL and soft-reset the badge. With
this module FaleraOS reads framed requests from that stream itself, once
per frame, and answers them without leaving the run loop. The badge
stays on screen; nothing restarts.

The wire (docs/USB.md "Live link"; cli/oreo_cli/live.py is the other end):

  computer -> badge   #@OLH {json}             a request: op, id, fields
                      #@OLD <seq> <base64>     a chunk of the file being put
  badge -> computer   @OLB {json}              a reply, progress or error (same id)
                      @OLD <seq> <base64>      a chunk of a file or screen being sent

One line per message, ASCII only. Every host line starts with '#', so a
line that reaches the MicroPython REPL instead (FaleraOS stopped, or an
older FaleraOS that does not read the stream) is a Python comment and does
nothing. The protocol never sends byte 0x03, so Ctrl-C keeps working:
this module never touches micropython.kbd_intr(), a Ctrl-C still raises
KeyboardInterrupt in FaleraOS and drops to the REPL, and the raw REPL
path of the CLI (and mpremote) works as before. Reading the stream every
frame also keeps MicroPython's 259-byte stdin ring empty, so a Ctrl-C is
never stuck behind unread bytes.

Reading never blocks: select.poll() says a byte is waiting before each
one-byte readinto(), and a frame stops reading after a few milliseconds
(more while a file arrives). Slow work never runs inside a frame:
storage scans, hashing, App Store downloads and recursive deletes run on
a worker thread (inline a frame later without _thread); Wi-Fi scans and
joins use the driver's non-blocking begin/poll calls; files and the
screen are streamed a few lines per frame.

Trust: USB only. Whoever holds the cable already has the REPL, so the
link adds no new power; it never listens on Wi-Fi or Bluetooth.

The run loop calls tick() once per frame and draw() before present; the
Transfer app (OWNS_USB_CONSOLE) reads the stream itself while it is open,
and the link steps aside. Server takes injectable writer, clock and root
so tests/test_livelink.py and cli/tests/test_live.py run it on a host.
"""

import json
import time

try:
    import binascii
except ImportError:          # pragma: no cover
    import ubinascii as binascii

try:
    import hashlib
except ImportError:          # pragma: no cover
    hashlib = None

try:
    import os as _os
except ImportError:          # pragma: no cover
    import uos as _os

VERSION = 1                  # bump on any change to the wire
HOST = b"#@OLH "
HOST_DATA = b"#@OLD "
BADGE = "@OLB "
BADGE_DATA = "@OLD "

MAX_LINE = 2304              # longest host line accepted
CHUNK_IN = 1536              # file bytes per host data line (2048 base64)
CHUNK_OUT = 3072             # file bytes per badge data line (4096 base64)
READ_MAX = 4096              # largest `read` answered inline
RESERVE = 64 * 1024          # flash kept free by `put` (the CLI's FREE_RESERVE)

SESSION_MS = 15000           # a computer counts as present this long after a line
PUT_IDLE_MS = 20000          # silence allowed in the middle of a file
READ_BUDGET_MS = 6           # serial reading per frame, idle or between requests
PUT_BUDGET_MS = 30           # ... while a file arrives
REALTIME_BUDGET_MS = 3       # ... under a realtime app (video)
WRITE_BUDGET_MS = 20         # streaming out per frame
JOB_STACK = 16 * 1024        # TLS handshakes need more than the default stack
OVERLAY_H = 22

OPS = ("hello", "ping", "bye", "abort", "info", "stat", "ls", "free", "mkdirs",
       "read", "get", "put", "sha256", "rm", "screen", "wifi_status", "wifi_scan",
       "wifi_connect", "wifi_saved", "wifi_save", "wifi_forget", "wifi_online",
       "apps", "apps_refresh", "app_install", "app_remove", "apps_changed",
       "profile_read", "profile_write")


def _ticks():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.monotonic() * 1000)


def _diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _b64(data):
    return binascii.b2a_base64(data).decode().strip()


def _hex(digest):
    return binascii.hexlify(digest).decode()


def _is_dir(mode):
    return bool(mode & 0x4000)


def _errno(e):
    return e.args[0] if getattr(e, "args", None) else 0


class LinkError(Exception):
    """A request that fails with a protocol error code."""

    def __init__(self, code, msg, **extra):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg
        self.extra = extra


# ── reading the USB console without blocking ────────────────────────────

class Reader:
    """Complete lines from sys.stdin, never blocking (the same reader as
    the Transfer app's link.UsbLink, minus its Ctrl-C switch: the live
    link leaves Ctrl-C alone)."""

    def __init__(self, max_line=MAX_LINE, stream=None, poller=None):
        if stream is None:
            import sys
            import select
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            poller = select.poll()
            poller.register(sys.stdin, select.POLLIN)
        self._stream = stream
        self._poller = poller
        self._one = bytearray(1)
        self._buf = bytearray()
        self._skip = False
        self.max_line = max_line

    def ready(self):
        for _ in self._poller.ipoll(0):
            return True
        return False

    def reset(self):
        self._buf = bytearray()
        self._skip = False

    def read_lines(self, budget_ms=READ_BUDGET_MS, max_lines=8):
        """Lines (bytes, no newline) that arrived; an over-long line comes
        back as None once its end is seen."""
        lines = []
        buf = self._buf
        one = self._one
        rd = self._stream.readinto
        ready = self.ready
        limit = self.max_line
        t0 = _ticks()
        n = 0
        while ready():
            if not rd(one):
                break
            n += 1
            c = one[0]
            if c == 10:
                if self._skip:
                    self._skip = False
                    lines.append(None)
                else:
                    if buf and buf[-1] == 13:
                        buf = buf[:-1]
                    lines.append(bytes(buf))
                buf = bytearray()
                if len(lines) >= max_lines:
                    break
            elif not self._skip:
                if len(buf) >= limit:
                    self._skip = True
                    buf = bytearray()
                else:
                    buf.append(c)
            if not (n & 63) and _diff(_ticks(), t0) >= budget_ms:
                break
        self._buf = buf
        return lines


# ── tasks: requests that take more than one frame ───────────────────────

class Task:
    title = None             # shown on the badge while it runs, if set
    detail = ""
    receiving = False

    def __init__(self, srv, rid):
        self.srv = srv
        self.rid = rid
        self.finished = False

    def step(self, budget_ms):
        return self.finished

    def cancel(self):
        self.finished = True

    def stoppable(self):
        """False while a worker thread still runs (it cannot be stopped)."""
        return True

    def progress(self):
        return None


class Job(Task):
    """fn(progress) on a worker thread; its dict result is the reply.
    progress(done, total, label) may be called from the worker: events
    are queued and sent by step() on the UI thread, so only the UI
    thread writes to the console."""

    def __init__(self, srv, rid, op, fn, title=None, detail=""):
        Task.__init__(self, srv, rid)
        self.op = op
        self.fn = fn
        self.title = title
        self.detail = detail
        self.events = []
        self.last = None
        self.done = False
        self.cancelled = False
        self.result = None
        self.error = None
        self.inline = True

    def start(self, threaded=True):
        self.inline = True
        if threaded:
            try:
                import _thread
                old = None
                try:
                    old = _thread.stack_size(JOB_STACK)
                except Exception:
                    old = None
                try:
                    _thread.start_new_thread(self._run, ())
                    self.inline = False
                finally:
                    if old is not None:
                        try:
                            _thread.stack_size(old)
                        except Exception:
                            pass
            except Exception:
                self.inline = True
        return self

    def _report(self, done, total, label=""):
        ev = (done, total, label or "")
        self.last = ev
        self.events.append(ev)
        if len(self.events) > 4:
            self.events.pop(0)

    def _run(self):
        try:
            self.result = self.fn(self._report)
        except Exception as e:
            self.error = e
        self.done = True

    def step(self, budget_ms):
        if self.inline and not self.done:
            self._run()          # a frame after the request (no _thread)
        while self.events:
            done, total, label = self.events.pop(0)
            if not self.cancelled:
                self.srv.send("progress", self.rid, done=done, total=total, label=label)
        if not self.done:
            return False
        if not self.cancelled:
            e = self.error
            if e is None:
                self.srv.send(self.op, self.rid, **(self.result or {}))
            elif isinstance(e, LinkError):
                self.srv.error(self.rid, e.code, e.msg, **e.extra)
            elif isinstance(e, OSError):
                self.srv.error(self.rid, "io", "errno %s" % _errno(e), errno=_errno(e))
            else:
                self.srv.error(self.rid, "fail", str(e) or e.__class__.__name__)
        self.finished = True
        return True

    def cancel(self):
        self.cancelled = True
        if self.done or self.inline:
            self.finished = True

    def stoppable(self):
        return self.done or self.inline

    def progress(self):
        if self.last is None:
            return None
        done, total, label = self.last
        if label:
            self.detail = label
        return (done / total) if total else None


class Poll(Task):
    """A non-blocking state machine on the UI thread: poll() returns None
    while it runs, else (op, fields) or raises LinkError."""

    def __init__(self, srv, rid, poll, timeout_ms, title=None, detail="", on_timeout=None):
        Task.__init__(self, srv, rid)
        self.poll = poll
        self.title = title
        self.detail = detail
        self.t0 = srv.now()
        self.timeout_ms = timeout_ms
        self.on_timeout = on_timeout

    def step(self, budget_ms):
        try:
            r = self.poll()
            if r is None and _diff(self.srv.now(), self.t0) > self.timeout_ms:
                r = self.on_timeout() if self.on_timeout else None
                if r is None:
                    raise LinkError("timeout", "the badge gave up waiting")
        except LinkError as e:
            self.srv.error(self.rid, e.code, e.msg, **e.extra)
            self.finished = True
            return True
        except Exception as e:
            self.srv.error(self.rid, "fail", str(e) or e.__class__.__name__)
            self.finished = True
            return True
        if r is None:
            return False
        self.srv.send(r[0], self.rid, **r[1])
        self.finished = True
        return True


class Sender(Task):
    """Streams bytes to the computer as @OLD lines, a few per frame, and
    ends with an `eof` reply carrying the count and the SHA-256."""

    def __init__(self, srv, rid, total, read, close=None, title=None, detail=""):
        Task.__init__(self, srv, rid)
        self.total = total
        self.read = read         # read(n) -> bytes
        self.close = close
        self.sent = 0
        self.seq = 0
        self.h = hashlib.sha256() if hashlib is not None else None
        self.title = title
        self.detail = detail

    def step(self, budget_ms):
        t0 = _ticks()
        while self.sent < self.total:
            piece = self.read(min(CHUNK_OUT, self.total - self.sent))
            if not piece:
                break
            if self.h is not None:
                self.h.update(piece)
            self.srv.data(self.seq, piece)
            self.seq += 1
            self.sent += len(piece)
            if _diff(_ticks(), t0) >= budget_ms:
                return False
        self._end()
        self.srv.send("eof", self.rid, n=self.sent,
                      sha=_hex(self.h.digest()) if self.h is not None else None)
        self.finished = True
        return True

    def _end(self):
        if self.close is not None:
            try:
                self.close()
            except Exception:
                pass
            self.close = None

    def cancel(self):
        self._end()
        self.finished = True

    def progress(self):
        return (self.sent / self.total) if self.total else None


class Receiver(Task):
    """A file arriving from the computer: written to <path>.part, checked
    (size, SHA-256) and only then renamed over <path>."""
    receiving = True

    def __init__(self, srv, rid, path, size, sha, title=None, detail=""):
        Task.__init__(self, srv, rid)
        self.path = path
        self.tmp = path + ".part"
        self.size = size
        self.want = sha
        self.got = 0
        self.seq = 0
        self.h = hashlib.sha256() if (sha and hashlib is not None) else None
        self.f = srv._open(srv.real(self.tmp), "wb")
        self.t_ms = srv.now()
        self.title = title
        self.detail = detail

    def data(self, rest):
        """rest: b'<seq> <base64>' from a #@OLD line."""
        self.t_ms = self.srv.now()
        try:
            sp = rest.index(b" ")
            seq = int(rest[:sp])
            piece = binascii.a2b_base64(rest[sp + 1:])
        except Exception:
            return self.fail("data", "damaged chunk")
        if seq != self.seq:
            return self.fail("seq", "chunk %d arrived, %d expected" % (seq, self.seq))
        if not piece or len(piece) > CHUNK_IN or self.got + len(piece) > self.size:
            return self.fail("data", "chunk of the wrong size")
        try:
            self.f.write(piece)
        except OSError as e:
            return self.fail("io", "write failed (errno %s)" % _errno(e))
        if self.h is not None:
            self.h.update(piece)
        self.got += len(piece)
        self.seq += 1
        self.srv.send("ack", self.rid, seq=seq, got=self.got)
        if self.got == self.size:
            self._finish()

    def _finish(self):
        srv = self.srv
        try:
            self.f.close()
            self.f = None
            n = _os.stat(srv.real(self.tmp))[6]
            sha = _hex(self.h.digest()) if self.h is not None else None
            if n != self.size:
                return self.fail("size", "%d bytes written, %d expected" % (n, self.size))
            if self.want and sha is not None and sha != self.want:
                return self.fail("hash", "SHA-256 mismatch")
            try:
                _os.rename(srv.real(self.tmp), srv.real(self.path))
            except OSError:
                _os.remove(srv.real(self.path))
                _os.rename(srv.real(self.tmp), srv.real(self.path))
        except OSError as e:
            return self.fail("io", "could not save the file (errno %s)" % _errno(e))
        srv.send("done", self.rid, path=self.path, size=n, sha=sha)
        self.finished = True

    def fail(self, code, msg):
        self._drop()
        self.srv.error(self.rid, code, msg)
        self.finished = True

    def _drop(self):
        if self.f is not None:
            try:
                self.f.close()
            except Exception:
                pass
            self.f = None
        try:
            _os.remove(self.srv.real(self.tmp))
        except Exception:
            pass

    def step(self, budget_ms):
        if self.finished:
            return True
        if self.size == 0:
            self._finish()
            return True
        if _diff(self.srv.now(), self.t_ms) > PUT_IDLE_MS:
            self.fail("timeout", "the computer stopped sending")
        return self.finished

    def cancel(self):
        if not self.finished:
            self._drop()
        self.finished = True

    def progress(self):
        return (self.got / self.size) if self.size else None


# ── the server: one computer, one request at a time ─────────────────────

class Server:
    def __init__(self, write, root="", clock=None, threaded=True, os_version=None,
                 opener=None):
        self._write = write          # write(text): one line, newline added here
        self._open = opener or open
        self.root = root.rstrip("/")
        self._clock = clock or _ticks
        self.threaded = threaded
        self.os_version = os_version
        self.session = None          # the hello id while a computer is here
        self.last_ms = None
        self.task = None
        self.tasks_done = 0

    # wire
    def now(self):
        return self._clock()

    def real(self, path):
        return self.root + path if self.root else path

    def send(self, op, rid=None, **fields):
        fields["op"] = op
        if rid is not None:
            fields["id"] = rid
        self._write(BADGE + json.dumps(fields))

    def error(self, rid, code, msg, **fields):
        self.send("error", rid, code=code, msg=msg, **fields)

    def data(self, seq, piece):
        self._write(BADGE_DATA + str(seq) + " " + _b64(piece))

    # state
    def active(self):
        """A computer is using the link: a request is running, or one
        arrived in the last SESSION_MS."""
        if self.task is not None:
            return True
        return (self.session is not None and self.last_ms is not None
                and _diff(self.now(), self.last_ms) < SESSION_MS)

    def receiving(self):
        return self.task is not None and self.task.receiving

    def overlay(self):
        """(title, detail, fraction or None) while a titled task runs."""
        t = self.task
        if t is None or not t.title:
            return None
        frac = t.progress()
        return (t.title, t.detail, frac)

    def _drop_task(self):
        t = self.task
        if t is not None:
            t.cancel()
            if t.finished:
                self.task = None

    # input
    def feed(self, line):
        """One line from the USB console (None: an over-long line)."""
        if line is None:
            if self.receiving():
                self.task.fail("line", "line too long")
                self.task = None
            elif self.session is not None:
                self.error(None, "line", "line too long")
            return
        if line.startswith(HOST_DATA):
            self.last_ms = self.now()
            t = self.task
            if t is not None and t.receiving and not t.finished:
                t.data(line[len(HOST_DATA):])
                if t.finished:
                    self.task = None
                    self.tasks_done += 1
            elif self.session is not None:
                self.error(None, "seq", "no file is being sent")
            return
        i = line.find(HOST)
        if i < 0:
            return               # not ours: log echo, a stray byte
        try:
            req = json.loads(line[i + len(HOST):].decode())
            op = req["op"]
            rid = req.get("id")
        except Exception:
            self.error(None, "proto", "unreadable request")
            return
        self.last_ms = self.now()
        if op == "hello":
            self._hello(rid, req)
            return
        if self.session is None:
            self.error(rid, "hello", "say hello first")
            return
        if op == "ping":
            self.send("pong", rid)
            return
        if op == "bye":
            self._drop_task()
            self.session = None
            self.send("bye", rid)
            return
        if op == "abort":
            self._drop_task()
            self.send("aborted", rid)
            return
        if self.task is not None:
            self.error(rid, "busy", "another request is still running")
            return
        fn = getattr(self, "_op_" + op, None) if op in OPS else None
        if fn is None:
            self.error(rid, "unknown", "unknown request %s" % op)
            return
        try:
            fn(rid, req)
        except LinkError as e:
            self.error(rid, e.code, e.msg, **e.extra)
        except OSError as e:
            n = _errno(e)
            code = "missing" if n == 2 else "io"
            self.error(rid, code, "errno %s" % n, errno=n)
        except Exception as e:
            self.error(rid, "fail", str(e) or e.__class__.__name__)

    def tick(self, budget_ms=WRITE_BUDGET_MS):
        t = self.task
        if t is None:
            return
        try:
            done = t.step(budget_ms)
        except Exception as e:
            try:
                t.cancel()
            except Exception:
                pass
            self.error(t.rid, "fail", str(e) or e.__class__.__name__)
            done = True
        if done or t.finished:
            if self.task is t:
                self.task = None
            self.tasks_done += 1

    def _start(self, task):
        self.task = task
        if isinstance(task, Job):
            task.start(self.threaded)

    def _hello(self, rid, req):
        if self.task is not None:
            if not self.task.stoppable():
                self.error(rid, "busy", "the last request is still running")
                return
            self._drop_task()
        self.task = None
        v = req.get("v")
        if v != VERSION:
            self.session = None
            self.error(rid, "version", "live link v%d" % VERSION, v=VERSION)
            return
        self.session = req.get("id") or rid or 1
        self.send("hello", rid, v=VERSION, os=self.os_version, chunk=CHUNK_IN,
                  out=CHUNK_OUT, max_line=MAX_LINE, reserve=RESERVE, ops=list(OPS))

    # ── helpers ─────────────────────────────────────────────────────────

    def _path(self, req, key="path"):
        p = req.get(key)
        if not isinstance(p, str) or not p.startswith("/") or "\x00" in p:
            raise LinkError("path", "a badge path starts with /")
        return p

    def _stat(self, path):
        try:
            return _os.stat(self.real(path))
        except OSError:
            return None

    def _free(self):
        """(free, total) bytes, as the CLI's raw REPL path counts them."""
        s = _os.statvfs(self.real("/"))
        bs = s[1] or s[0]
        return bs * s[4], bs * s[2]

    def _listdir(self, path):
        real = self.real(path)
        rows = []
        ilist = getattr(_os, "ilistdir", None)
        if ilist is not None:
            for x in ilist(real):
                rows.append([x[0], 1 if _is_dir(x[1]) else 0, x[3] if len(x) > 3 else 0])
        else:                     # a host
            for name in sorted(_os.listdir(real)):
                st = _os.stat(real + "/" + name if real != "/" else "/" + name)
                rows.append([name, 1 if _is_dir(st[0]) else 0,
                             0 if _is_dir(st[0]) else st[6]])
        return rows

    # ── files ───────────────────────────────────────────────────────────

    def _op_stat(self, rid, req):
        st = self._stat(self._path(req))
        if st is None:
            self.send("stat", rid, missing=1)
        else:
            self.send("stat", rid, dir=_is_dir(st[0]), size=st[6])

    def _op_ls(self, rid, req):
        p = self._path(req)
        st = self._stat(p)
        if st is None:
            raise LinkError("missing", "%s: not found" % p)
        if not _is_dir(st[0]):
            self.send("ls", rid, file=[p.rsplit("/", 1)[-1], 0, st[6]])
            return
        self.send("ls", rid, ls=self._listdir(p))

    def _op_free(self, rid, req):
        free, total = self._free()
        self.send("free", rid, free=free, total=total)

    def _op_mkdirs(self, rid, req):
        p = self._path(req)
        cur = ""
        for part in p.split("/"):
            if not part:
                continue
            cur = cur + "/" + part
            try:
                _os.mkdir(self.real(cur))
            except OSError:
                pass
        self.send("ok", rid)

    def _op_read(self, rid, req):
        """The first n bytes of a file (small reads, e.g. a header)."""
        p = self._path(req)
        n = int(req.get("n") or 0)
        if not 0 <= n <= READ_MAX:
            raise LinkError("size", "read at most %d bytes" % READ_MAX)
        try:
            f = self._open(self.real(p), "rb")
        except OSError:
            self.send("read", rid, missing=1)
            return
        try:
            data = f.read(n)
        finally:
            f.close()
        self.send("read", rid, data=_b64(data) if data else "", n=len(data))

    def _op_get(self, rid, req):
        p = self._path(req)
        st = self._stat(p)
        if st is None:
            raise LinkError("missing", "%s: not found" % p)
        if _is_dir(st[0]):
            raise LinkError("isdir", "%s is a folder" % p)
        f = self._open(self.real(p), "rb")
        total = st[6]
        self.send("file", rid, size=total, chunk=CHUNK_OUT)
        self._start(Sender(self, rid, total, f.read, f.close,
                           title=req.get("title"), detail=req.get("detail") or ""))

    def _op_put(self, rid, req):
        p = self._path(req)
        size = req.get("size")
        if not isinstance(size, int) or size < 0:
            raise LinkError("size", "bad size")
        sha = req.get("sha256")
        if sha is not None and (not isinstance(sha, str) or len(sha) != 64):
            raise LinkError("sha", "bad sha256")
        st = self._stat(p)
        if st is not None and _is_dir(st[0]):
            raise LinkError("isdir", "%s is a folder" % p)
        if req.get("check_space", True):
            free, _ = self._free()
            have = free + (st[6] if st is not None else 0)
            if size + RESERVE > have:
                raise LinkError("no_space", "%d bytes free" % max(0, free - RESERVE),
                                free=free)
        try:
            t = Receiver(self, rid, p, size, sha, title=req.get("title"),
                         detail=req.get("detail") or "")
        except OSError as e:
            raise LinkError("io", "cannot write %s (errno %s)" % (p, _errno(e)))
        self.send("ready", rid, chunk=CHUNK_IN)
        self._start(t)

    def _op_sha256(self, rid, req):
        p = self._path(req)
        real = self.real(p)

        def work(progress):
            if hashlib is None:
                return {"sha": None}
            h = hashlib.sha256()
            f = self._open(real, "rb")
            try:
                while True:
                    c = f.read(2048)
                    if not c:
                        break
                    h.update(c)
            finally:
                f.close()
            return {"sha": _hex(h.digest())}
        self._start(Job(self, rid, "sha256", work))

    def _op_rm(self, rid, req):
        p = self._path(req)
        recursive = bool(req.get("recursive"))
        st = self._stat(p)
        if st is None:
            raise LinkError("missing", "%s: not found" % p)
        if _is_dir(st[0]) and not recursive:
            raise LinkError("isdir", "%s is a folder" % p)
        srv = self

        def rm(path):
            real = srv.real(path)
            if _is_dir(_os.stat(real)[0]):
                for row in srv._listdir(path):
                    rm(path.rstrip("/") + "/" + row[0])
                if path != "/":
                    _os.rmdir(real)
            else:
                _os.remove(real)

        def work(progress):
            rm(p)
            return {}
        self._start(Job(self, rid, "ok", work))

    # ── the screen ──────────────────────────────────────────────────────

    def _display(self):
        d, src = None, None
        try:
            from oreoWare import display as dm
            d = dm.current()
            src = "display"
        except Exception:
            pass
        if d is None:
            try:
                from oreoOS import busy
                d = busy._display
                src = src or "busy"
            except Exception:
                pass
        return d, src

    def _op_screen(self, rid, req):
        """The frame on the panel, as big-endian RGB565. tick() runs right
        after the frame was presented, so a copy taken here is the whole
        frame; without memory for a copy it is sent from the live buffer
        (and may mix two frames)."""
        d, src = self._display()
        fb = getattr(d, "_buf", None) if d is not None else None
        if fb is None:
            raise LinkError("none", "no display yet")
        try:
            from oreoOS import api
            w, h = api.SCREEN_W, api.SCREEN_H
        except Exception:
            w, h = 320, 240
        live = False
        try:
            snap = bytes(fb)
        except MemoryError:
            snap = fb
            live = True
        mv = memoryview(snap)
        n = len(mv)
        pos = [0]

        def read(k):
            i = pos[0]
            pos[0] = i + k
            return mv[i:i + k]
        self.send("screen", rid, w=w, h=h, n=n, src=src, live=live, chunk=CHUNK_OUT)
        self._start(Sender(self, rid, n, read))

    # ── device info ─────────────────────────────────────────────────────

    def _op_info(self, rid, req):
        def work(progress):
            import gc
            import sys
            r = {"live": VERSION}
            try:
                u = _os.uname()
                r["machine"] = u.machine
                r["fw"] = u.version
                r["release"] = u.release
            except Exception:
                pass
            try:
                v = sys.implementation.version
                r["mpy"] = "%d.%d.%d" % (v[0], v[1], v[2])
            except Exception:
                r["mpy"] = None
            try:
                from oreoOS import config as c
                r["os"] = c.VERSION
                r["date"] = getattr(c, "RELEASE_DATE", "")
                r["bt_auto"] = bool(getattr(c, "BT_AUTO_ENABLE", False))
            except Exception:
                r["os"] = self.os_version
            try:
                from oreoOS import storage as s
                r["storage"] = s.usage()
            except Exception:
                try:
                    free, total = self._free()
                    r["storage"] = {"stats": {"total": total, "free": free,
                                              "used": total - free}, "buckets": None}
                except Exception:
                    r["storage"] = None
            r["wifi"] = self._wifi_status_dict()
            try:
                from oreoWare import battery as b
                r["battery"] = {"pct": b.read_percent(), "v": b.read_voltage()}
            except Exception:
                r["battery"] = None
            gc.collect()
            try:
                r["mem_free"] = gc.mem_free()
            except Exception:
                pass
            return {"info": r}
        self._start(Job(self, rid, "info", work))

    # ── Wi-Fi (the driver's non-blocking calls, on the UI thread) ───────

    def _wifi(self):
        from oreoWare import wifi
        return wifi

    def _wifi_status_dict(self):
        try:
            w = self._wifi()
            up = bool(w.is_connected())
            r = {"up": up, "ip": w.ip() if up else None, "saved": len(w.list_saved())}
            if up:
                try:
                    r["ssid"] = w.ssid()
                    r["rssi"] = w.rssi()
                except Exception:
                    pass
            return r
        except Exception:
            return None

    def _op_wifi_status(self, rid, req):
        self.send("wifi_status", rid, wifi=self._wifi_status_dict())

    def _op_wifi_saved(self, rid, req):
        w = self._wifi()
        out = []
        for n in w.list_saved():
            out.append({"ssid": n.get("ssid"), "priority": n.get("priority"),
                        "metered": bool(n.get("metered")), "last": bool(n.get("last")),
                        "open": not n.get("password")})
        self.send("wifi_saved", rid, saved=out)

    def _op_wifi_save(self, rid, req):
        w = self._wifi()
        ok = w.add_saved(req.get("ssid") or "", req.get("password") or "",
                         int(req.get("priority") or 10))
        self.send("wifi_save", rid, saved=bool(ok))

    def _op_wifi_forget(self, rid, req):
        w = self._wifi()
        ssid = req.get("ssid") or ""
        deploy = False
        try:
            deploy = ssid in [n.get("ssid") for n in w._secrets_networks()]
        except Exception:
            pass
        self.send("wifi_forget", rid, removed=bool(w.remove_saved(ssid)), deploy=deploy)

    def _op_wifi_scan(self, rid, req):
        w = self._wifi()
        try:
            w.auto_stop()
        except Exception:
            pass
        try:
            seq0 = w.scan_seq()
        except Exception:
            seq0 = None
        if not w.scan_start():
            raise LinkError("radio", w.scan_error() or "radio error")

        def poll():
            r = w.scan_poll()
            if r == "busy":
                return None
            # netsvc's auto-connect may have collected our scan first: the
            # results are in nearby() all the same.
            if r == "done" or (r == "idle" and seq0 is not None and w.scan_seq() != seq0):
                return ("wifi_scan", {"nets": w.nearby(), "saved": list(w.saved_ssids())})
            raise LinkError("scan", w.scan_error() or r)
        self._start(Poll(self, rid, poll, int(req.get("timeout_ms") or 20000),
                         title="Wi-Fi scan", detail="asked by the computer"))

    def _op_wifi_connect(self, rid, req):
        w = self._wifi()
        ssid = req.get("ssid") or ""
        pw = req.get("password") or ""
        save = bool(req.get("save", True))
        timeout = int(req.get("timeout_ms") or 15000)
        try:
            w.auto_stop()
        except Exception:
            pass
        if not w.connect_begin(ssid, pw, timeout):
            r0 = w.connect_poll()
        else:
            r0 = None
        state = {"pw": pw}
        del pw

        def finish(r):
            ok = r == w.CONN_OK
            saved = False
            if ok and save:
                saved = bool(w.add_saved(ssid, state["pw"]))
                try:
                    w._mark_last(ssid)
                except Exception:
                    pass
            state["pw"] = None
            return ("wifi_connect", {"result": r, "ip": w.ip() if ok else None,
                                     "saved": saved})

        def poll():
            r = r0 if r0 is not None else w.connect_poll()
            if r == w.CONN_BUSY:
                return None
            return finish(r)

        def timed_out():
            state["pw"] = None
            return ("wifi_connect", {"result": "timeout", "ip": None, "saved": False})
        self._start(Poll(self, rid, poll, timeout + 5000, title="Joining Wi-Fi",
                         detail=ssid, on_timeout=timed_out))

    def _op_wifi_online(self, rid, req):
        """Bring Wi-Fi up from the saved networks (App Store, updates)."""
        w = self._wifi()
        if w.is_connected():
            self.send("wifi_online", rid, online=True, ip=w.ip())
            return
        w.auto_begin()
        t0 = self.now()

        def poll():
            if w.is_connected():
                return ("wifi_online", {"online": True, "ip": w.ip()})
            try:
                w.auto_tick()        # rate-limited inside; netsvc drives it too
                state = w.auto_state()
            except Exception:
                state = None
            if state in ("no_network", "off") and _diff(self.now(), t0) > 1500:
                return ("wifi_online", {"online": False, "ip": None})
            return None

        def timed_out():
            return ("wifi_online", {"online": False, "ip": None})
        self._start(Poll(self, rid, poll, int(req.get("timeout_ms") or 60000),
                         title="Wi-Fi", detail="connecting", on_timeout=timed_out))

    # ── apps (oreoOS.store; downloads run on the worker) ────────────────

    def _op_apps(self, rid, req):
        def work(progress):
            from oreoOS import store as S
            inst = []
            try:
                names = _os.listdir(self.real("/apps"))
            except OSError:
                names = []
            for n in sorted(names):
                if n in S.HIDDEN_INSTALLED:
                    continue
                m = S.installed_manifest(n)
                if not m:
                    continue
                inst.append({"id": n, "name": m.get("name", n),
                             "version": S.installed_version(n), "default": S.is_default(n)})
            cat = []
            for e in S.catalogue()[0]:
                cat.append({"id": e["id"], "name": e["name"], "version": e["version"],
                            "size": e["size"], "tagline": e["tagline"],
                            "category": e["category"], "state": S.app_state(e)})
            return {"installed": inst, "catalogue": cat}
        self._start(Job(self, rid, "apps", work))

    def _op_apps_refresh(self, rid, req):
        def work(progress):
            from oreoOS import store as S
            ok = S.refresh()
            return {"ok": bool(ok), "error": S.last_error()}
        self._start(Job(self, rid, "apps_refresh", work, title="App Store",
                        detail="fetching the catalogue"))

    def _op_app_install(self, rid, req):
        app_id = req.get("app") or ""

        def work(progress):
            from oreoOS import store as S
            ok, msg = S.install(app_id, progress)
            return {"ok": bool(ok), "msg": msg}
        self._start(Job(self, rid, "app_install", work,
                        title=req.get("title") or "Installing app",
                        detail=req.get("detail") or app_id))

    def _op_app_remove(self, rid, req):
        app_id = req.get("app") or ""

        def work(progress):
            from oreoOS import store as S
            ok, msg = S.uninstall(app_id)
            return {"ok": bool(ok), "msg": msg}
        self._start(Job(self, rid, "app_remove", work, title="Removing app", detail=app_id))

    def _op_apps_changed(self, rid, req):
        """Files under /apps changed from the computer (`falera app
        install`): forget cached manifests so the drawer lists them."""
        try:
            from oreoOS import store as S
            S.forget_installed()
            S._invalidate_launcher()
        except Exception:
            pass
        self.send("ok", rid)

    # ── owner profile (oreoOS.profile) ──────────────────────────────────

    def _profile_path(self):
        try:
            from oreoOS import profile
            return profile.PATH
        except Exception:
            return "/profile.json"

    def _read_json(self, path):
        f = self._open(self.real(path))
        try:
            return json.loads(f.read())
        finally:
            f.close()

    def _op_profile_read(self, rid, req):
        """{'file', 'state', 'secrets'} like the CLI's raw REPL read, with
        the secret key (OWM_API_KEY) replaced by True when it is set."""
        keys = req.get("keys") or []
        secret = req.get("secret")
        path = self._profile_path()

        def pick(d):
            o = {}
            for k in keys:
                v = d.get(k)
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                o[k] = bool(v) if k == secret else v
            return o
        f, st = None, "missing"
        for p in (path, path + ".tmp"):
            try:
                d = self._read_json(p)
            except OSError:
                continue
            except Exception:
                st = "bad"
                continue
            if isinstance(d, dict):
                f = pick(d)
                st = "ok" if p == path else "tmp"
                break
            st = "bad"
        s = {}
        try:
            import secrets as _s
            s = pick(dict((k, getattr(_s, k, None)) for k in keys))
        except Exception:
            pass
        self.send("profile_read", rid, file=f, state=st, secrets=s)

    def _op_profile_write(self, rid, req):
        """Merge updates ('' removes a key) into /profile.json, atomically
        (staged in .tmp, read back, renamed), then make oreoOS.profile
        read it again so apps see it without a restart."""
        updates = req.get("updates") or {}
        path = self._profile_path()
        d = {}
        for p in (path, path + ".tmp"):
            try:
                x = self._read_json(p)
            except Exception:
                continue
            if isinstance(x, dict):
                d = x
                break
        tz_before = d.get("TIMEZONE_OFFSET")
        for k in updates:
            if updates[k] == "":
                d.pop(k, None)
            else:
                d[k] = updates[k]
        updates = None
        t = json.dumps(d)
        tmp = self.real(path + ".tmp")
        f = self._open(tmp, "w")
        try:
            f.write(t)
        finally:
            f.close()
        if self._read_json(path + ".tmp") != d:
            _os.remove(tmp)
            raise LinkError("io", "read-back mismatch")
        try:
            _os.rename(tmp, self.real(path))
        except OSError:
            try:
                _os.remove(self.real(path))
            except OSError:
                pass
            _os.rename(tmp, self.real(path))
        reloaded = False
        try:
            from oreoOS import profile
            profile.reload()
            reloaded = True
        except Exception:
            pass
        synced = False
        if d.get("TIMEZONE_OFFSET") != tz_before:
            try:
                from oreoOS import netsvc
                synced = bool(netsvc.request_sync())
            except Exception:
                pass
        self.send("profile_write", rid, keys=sorted(k for k in d if d[k] not in (None, "")),
                  reloaded=reloaded, time_sync=synced)


# ── run-loop glue ───────────────────────────────────────────────────────

_reader = None
_server = None
_off = False                 # setup failed: stay out of the way
_owned = False               # an app reads the console itself (Transfer)


def _write_line(text):
    import sys
    sys.stdout.write(text + "\n")


def _setup():
    global _reader, _server, _off
    try:
        try:
            from oreoOS.config import VERSION as osv
        except Exception:
            osv = None
        _reader = Reader(MAX_LINE)
        _server = Server(_write_line, os_version=osv)
    except Exception as e:
        _off = True
        try:
            print("[live] off: %s" % e)
        except Exception:
            pass


def tick(app=None, realtime=False):
    """Once per frame, after present. Reads what the computer sent, runs
    a slice of the current request. Returns True while a computer is
    using the link (the run loop keeps the badge awake then)."""
    global _owned
    if _off:
        return False
    if app is not None and getattr(app, "OWNS_USB_CONSOLE", False):
        if not _owned and _server is not None:
            _server._drop_task()
            _server.session = None
        _owned = True
        return False
    if _server is None:
        _setup()
        if _off:
            return False
    if _owned:
        _owned = False
        _reader.reset()
    srv = _server
    if realtime and not srv.receiving():
        budget = REALTIME_BUDGET_MS
    else:
        budget = PUT_BUDGET_MS if srv.receiving() else READ_BUDGET_MS
    for line in _reader.read_lines(budget, 8):
        srv.feed(line)
    srv.tick(REALTIME_BUDGET_MS if realtime else WRITE_BUDGET_MS)
    return srv.active()


def pending():
    """A byte from the computer is waiting (power.py wakes on it)."""
    try:
        return _reader is not None and not _owned and _reader.ready()
    except Exception:
        return False


def overlay():
    return None if _server is None else _server.overlay()


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "~"


def draw(display):
    """A strip at the bottom of the frame while the computer copies
    files or installs apps. Returns True when it drew."""
    o = overlay()
    if o is None:
        return False
    title, detail, frac = o
    from oreoOS import api, theme
    w, h = api.SCREEN_W, api.SCREEN_H
    y = h - OVERLAY_H
    display.rect(0, y, w, OVERLAY_H, theme.TEXT_BRIGHT, fill=True)
    text = "USB  " + title + ((": " + detail) if detail else "")
    if frac is not None:
        pct = " %d%%" % int(min(1.0, max(0.0, frac)) * 100)
    else:
        pct = ""
    display.text(_clip(text, (w - 12) // 8 - len(pct)) + pct, 6, y + 3, api.WHITE)
    display.rect(6, y + 15, w - 12, 3, theme.MUTED, fill=True)
    if frac is not None:
        display.rect(6, y + 15, int((w - 12) * min(1.0, max(0.0, frac))), 3,
                     theme.PRIMARY, fill=True)
    return True
