#!/usr/bin/env python3
"""Server-side tests: drive tether-attach in a pseudo-terminal against
test/fake-claude, with private state, config and working directories.
Needs dtach and python3. Run: python3 test/test_server.py"""
import fcntl, os, pty, re, select, shutil, signal, struct, subprocess, sys, tempfile, termios, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACH = os.path.join(ROOT, "bin", "tether-attach")


def plain(b):
    s = b.decode("utf-8", "replace")
    return re.sub(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?<>=]*[A-Za-z~]", "", s)


class Session:
    """tether-attach in a pty, like a terminal window"""

    def __init__(self, env, *args):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve("/bin/sh", ["sh", ATTACH, *args], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.raw = b""

    def read_until(self, pattern, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if re.search(pattern, plain(self.raw)):
                return True
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.raw += data
        return bool(re.search(pattern, plain(self.raw)))

    def send(self, data):
        os.write(self.fd, data)

    def detach(self):
        self.send(b"\x1c")
        return self.finish()

    def finish(self, timeout=10):
        self.read_until(r"Detached|Claude exited", timeout)
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        os.close(self.fd)
        return plain(self.raw)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tether-test-")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.log = os.path.join(self.tmp, "fake.log")
        self.state = os.path.join(self.tmp, "state")
        self.env = dict(
            os.environ,
            TERM="xterm-256color",
            SHELL="/bin/sh",
            TETHER_STATE_DIR=self.state,
            TETHER_CLAUDE=os.path.join(ROOT, "test", "fake-claude"),
            CLAUDE_CONFIG_DIR=os.path.join(self.tmp, "claude"),
            FAKE_LOG=self.log,
        )
        self.env.pop("NO_COLOR", None)

    def tearDown(self):
        sock = os.path.join(self.state, "default.sock")
        subprocess.run(["pkill", "-f", "dtach -A " + sock], check=False)
        subprocess.run(["pkill", "-f", "dtach -A " + os.path.join(self.state, "other.sock")], check=False)
        time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def starts(self):
        with open(self.log) as f:
            return [l.rstrip("\n") for l in f]

    def wait_recorded(self, sid, timeout=4):
        end = time.time() + timeout                        # polled every 1 s at first
        while time.time() < end and self.recorded() != sid:
            time.sleep(0.1)
        self.assertEqual(self.recorded(), sid)

    def recorded(self, name="default"):
        try:
            with open(os.path.join(self.state, name + ".session")) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def master_alive(self, name="default"):
        sock = os.path.join(self.state, name + ".sock")
        return subprocess.run(["pgrep", "-f", "^dtach -A " + sock + " "], capture_output=True).returncode == 0

    def start(self, *args):
        s = Session(self.env, "-d", self.work, *args)
        self.assertTrue(s.read_until(r"FAKE READY session=(\S+)"), plain(s.raw))
        return s, re.search(r"FAKE READY session=(\S+)", plain(s.raw)).group(1)

    def test_fresh_start_survives_detach(self):
        s, sid = self.start()
        self.assertIn("Starting Claude session 'default'", plain(s.raw))
        self.assertEqual(self.starts(), [""])            # no --resume/--continue in an empty dir
        out = s.detach()
        self.assertIn("Detached", out)
        self.assertTrue(self.master_alive())

    def test_reattach_redraws_every_time_and_restores_modes(self):
        s, sid = self.start()
        s.detach()
        for _ in range(2):                                 # same size twice: still redrawn
            r = Session(self.env)
            self.assertTrue(r.read_until(r"FAKE REDRAW"), plain(r.raw))
            self.assertIn("Reattaching to Claude session 'default'", plain(r.raw))
            self.assertIn(b"\x1b[>5u", r.raw)              # Claude's startup modes re-sent
            r.detach()
        self.assertEqual(len(self.starts()), 1)           # one Claude the whole time

    def test_records_conversation_and_follows_clear(self):
        s, sid = self.start()
        end = time.time() + 8
        while time.time() < end and self.recorded() != sid:
            time.sleep(0.2)
        self.assertEqual(self.recorded(), sid)
        s.send(b"n")                                      # like /clear: new conversation
        self.assertTrue(s.read_until(r"FAKE NEW session=(\S+)"))
        new = re.search(r"FAKE NEW session=(\S+)", plain(s.raw)).group(1)
        end = time.time() + 8
        while time.time() < end and self.recorded() != new:
            time.sleep(0.2)
        self.assertEqual(self.recorded(), new)
        s.detach()

    def test_restart_resumes_exact_conversation(self):
        s, first = self.start()
        s.send(b"n")
        s.read_until(r"FAKE NEW session=(\S+)")
        current = re.search(r"FAKE NEW session=(\S+)", plain(s.raw)).group(1)
        s.detach()
        r = Session(self.env)                             # reattach records it too
        r.read_until("FAKE REDRAW")
        r.send(b"q")
        self.assertIn("Claude exited", r.finish())
        # make the *other* conversation newest, so --continue would pick it
        claude = self.env["CLAUDE_CONFIG_DIR"]
        proj = [os.path.join(dp, f) for dp, _, fs in os.walk(claude) for f in fs if f.startswith(first)]
        os.utime(proj[0], None)
        n = Session(self.env)
        self.assertTrue(n.read_until(r"FAKE READY"), plain(n.raw))
        self.assertEqual(self.starts()[-1], "--resume " + current)
        self.assertIn("last prompt", plain(n.raw))
        n.detach()

    def test_missing_conversation_starts_fresh(self):
        s, sid = self.start()
        self.wait_recorded(sid)
        s.send(b"q")
        s.finish()
        with open(os.path.join(self.state, "default.session"), "w") as f:
            f.write("00000000-gone")
        n = Session(self.env)
        self.assertTrue(n.read_until(r"FAKE READY"))
        self.assertEqual(self.starts()[-1], "")            # not --continue
        self.assertIn("new conversation", plain(n.raw))
        n.detach()

    def test_failed_resume_starts_fresh(self):
        s, sid = self.start()
        self.wait_recorded(sid)
        s.send(b"q")
        s.finish()
        env = dict(self.env, FAKE_FAIL_RESUME="1")
        n = Session(env)
        self.assertTrue(n.read_until(r"FAKE READY"), plain(n.raw))
        self.assertIn("Could not resume", plain(n.raw))
        self.assertEqual(self.starts()[-2:], ["--resume " + sid, ""])
        n.detach()

    def test_named_sessions_are_independent(self):
        a, sa = self.start()
        a.detach()
        b, sb = self.start("other")
        self.assertIn("Starting Claude session 'other'", plain(b.raw))
        self.assertNotEqual(sa, sb)
        b.detach()
        self.assertTrue(self.master_alive("default") and self.master_alive("other"))

    def test_bad_input(self):
        r = subprocess.run(["sh", ATTACH, "bad/name"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run(["sh", ATTACH, "-d", "/nonexistent/dir"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such directory", r.stderr)


if __name__ == "__main__":
    if not shutil.which("dtach"):
        sys.exit("dtach is required")
    unittest.main(verbosity=2)
