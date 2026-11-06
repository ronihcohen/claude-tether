#!/usr/bin/env python3
"""Client-side tests: run bin/tether against a fake `ssh` that plays out
scripted scenarios (dropped link, instant failures, unreachable host, login
prompt). Runs anywhere; set TEST_BASH to pick the bash (default /bin/bash,
which is 3.2 on macOS). Run: python3 test/test_client.py"""
import os, shutil, signal, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TETHER = os.path.join(ROOT, "bin", "tether")
BASH = os.environ.get("TEST_BASH", "/bin/bash")

FAKE_SSH = r"""#!/bin/sh
# fake ssh: probes (BatchMode) answer per $FAKE_PROBE; sessions follow $FAKE_PLAN
echo "$*" >> "$FAKE_DIR/calls"
case "$*" in
  *BatchMode=yes*)
    case $FAKE_PROBE in
      ok) exit 0 ;;
      auth) echo "user@host: Permission denied (publickey,password)." >&2; exit 255 ;;
      *) echo "ssh: Could not resolve hostname host" >&2; exit 255 ;;
    esac ;;
esac
n=$(cat "$FAKE_DIR/n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$FAKE_DIR/n"
step=$(sed -n "${n}p" "$FAKE_DIR/plan")
for last; do :; done
case $step in
  drop) sleep 3.5; exit 255 ;;
  fast) exit 255 ;;
  ok|"") echo "REMOTE-RAN: $last"; exit 0 ;;
esac
"""


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tether-client-")
        fake_bin = os.path.join(self.tmp, "bin")
        os.makedirs(fake_bin)
        with open(os.path.join(fake_bin, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(fake_bin, "ssh"), 0o755)
        self.env = dict(
            os.environ,
            PATH=fake_bin + os.pathsep + os.environ["PATH"],
            FAKE_DIR=self.tmp,
            FAKE_PROBE="ok",
            TETHER_TAILSCALE="off",
            XDG_CONFIG_HOME=os.path.join(self.tmp, "config"),
        )
        for k in ("TETHER_HOST", "TETHER_SESSION", "TETHER_SSH_OPTS", "TETHER_REMOTE"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, *steps):
        with open(os.path.join(self.tmp, "plan"), "w") as f:
            f.write("\n".join(steps) + "\n")

    def run_tether(self, *args, timeout=40, **env):
        e = dict(self.env, **env)
        return subprocess.run([BASH, TETHER, *args], env=e, capture_output=True, text=True, timeout=timeout)

    def calls(self):
        with open(os.path.join(self.tmp, "calls")) as f:
            return f.read().splitlines()

    def test_reconnects_after_dropped_link(self):
        self.plan("drop", "ok")
        r = self.run_tether("host")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Connection lost", r.stdout)
        self.assertIn("Reconnecting in", r.stdout)
        self.assertEqual(r.stdout.count("REMOTE-RAN"), 1)
        self.assertIn("ServerAliveInterval=2", self.calls()[-1])

    def test_remote_command_is_quoted(self):
        self.plan("ok")
        r = self.run_tether("-s", "work", "-d", "my dir", "host")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(r"REMOTE-RAN: .local/bin/tether-attach -d my\ dir work", r.stdout)

    def test_local_home_path_becomes_remote_home(self):
        self.plan("ok")
        r = self.run_tether("-d", os.path.expanduser("~/src/app"), "host")
        # bash 5 sends \~/..., bash 3.2 ~/...; both mean the remote home
        self.assertRegex(r.stdout, r"tether-attach -d \\?~/src/app default")

    def test_gives_up_when_ssh_keeps_failing_at_once(self):
        self.plan("fast", "fast", "fast", "ok")
        r = self.run_tether("host")
        self.assertEqual(r.returncode, 1)
        self.assertIn("keeps failing", r.stderr)
        self.assertNotIn("REMOTE-RAN", r.stdout)

    def test_login_prompt_is_left_to_ssh(self):
        self.plan("ok")
        r = self.run_tether("host", FAKE_PROBE="auth")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Permission denied", r.stdout)
        self.assertIn("REMOTE-RAN", r.stdout)

    def test_unreachable_retries_until_interrupted(self):
        p = subprocess.Popen([BASH, TETHER, "host"], env=dict(self.env, FAKE_PROBE="down"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(4)
        p.send_signal(signal.SIGINT)
        out, _ = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 130)
        self.assertRegex(out, "unreachable|offline")
        self.assertIn("Reconnecting in", out)

    def test_host_from_config_file_and_missing_host(self):
        r = self.run_tether()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no host", r.stderr)
        os.makedirs(os.path.join(self.tmp, "config", "claude-tether"))
        with open(os.path.join(self.tmp, "config", "claude-tether", "config"), "w") as f:
            f.write("TETHER_HOST=confighost\nTETHER_SESSION=fromconfig\n")
        self.plan("ok")
        r = self.run_tether()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Reach confighost", r.stdout)
        self.assertIn("tether-attach fromconfig", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
