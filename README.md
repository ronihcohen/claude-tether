# claude-tether

Keep [Claude Code](https://docs.anthropic.com/en/docs/claude-code) running on
another machine, and connect to it from your terminal over plain ssh, with
**native scrollback, selection and copy/paste**. It survives dropped
connections and a closed lid, and reconnects by itself.

```
$ tether myserver
✓ Reach myserver (0.2s)
▸ Reattaching to Claude session 'default' (up since 09:12:40)
  detach: Ctrl-\
```

Not affiliated with Anthropic. Claude Code is Anthropic's product; this is a
small set of shell scripts around it.

## Why

The usual ways to keep a remote Claude Code session alive all cost you the
terminal:

| | Survives disconnect | Native scroll/copy | Auto-reconnect |
| --- | --- | --- | --- |
| plain ssh | no | yes | no |
| ssh + tmux/screen | yes | no: the multiplexer draws the screen, so you scroll and copy in its copy mode | no |
| mosh (+ tmux) | yes | no: mosh has no scrollback | yes |
| **claude-tether** (ssh + dtach) | yes | **yes** | **yes** |

[dtach](https://github.com/crigler/dtach) only keeps the process alive and
passes its output through untouched, so your terminal stays in charge. The
rest of claude-tether handles the cases where that's not enough on its own,
such as reattaching, redrawing, dropped links and terminal modes.

## Install

On the **server** (where Claude Code runs), install `dtach`
(`apt/dnf/pacman/brew install dtach`). Then, from a clone of this repo on your
**local** machine:

```sh
git clone https://github.com/ronihcohen/claude-tether && cd claude-tether
./install.sh client              # puts `tether` in ~/.local/bin
./install.sh server myserver     # puts tether-attach and tether-run in ~/.local/bin on myserver
```

If the local machine is also a server, `./install.sh server` with no host
installs the server side on it. `./install.sh uninstall` removes either side.

Requirements:
- local: bash (3.2+ is fine, so the stock macOS bash works), ssh
- server: POSIX sh, dtach, `pgrep`, Claude Code; python3 is optional and only
  adds a summary of the conversation being resumed

## Use

```sh
tether myserver                  # attach to the 'default' session, or start it
tether -s api myserver           # another, independent session
tether -d ~/src/app myserver     # working directory for a new session
```

- **Ctrl-\\** detaches, and Claude keeps running. Run `tether` again to come back.
- If the connection drops (Wi-Fi off, laptop asleep), `tether` notices in
  about 8 s, resets your terminal and reconnects by itself, to the same
  Claude. Ctrl-C stops the retries.
- Frozen right now? **Enter `~` `.`** drops the connection at once.
- If Claude exits (or the server reboots), the next `tether` starts it again
  on **the same conversation**.

Tip: `alias tc='tether myserver'`.

### Config

`~/.config/claude-tether/config` on the local machine, in shell syntax:

```sh
TETHER_HOST=myserver           # default host
TETHER_SESSION=default         # default session name
TETHER_SSH_OPTS="-p 2222"      # extra ssh options
TETHER_TAILSCALE=auto          # auto: if Tailscale is installed, start it first; off: never
TETHER_REMOTE=.local/bin/tether-attach   # server command, if installed elsewhere
```

On the server, in the environment `ssh` gives you:

```sh
TETHER_DIR=~/code                       # working directory for new sessions (default ~)
TETHER_CLAUDE_ARGS="--permission-mode auto"   # extra arguments for claude
TETHER_CLAUDE=/path/to/claude           # if claude isn't on PATH or in ~/.local/bin
TETHER_RESTORE_MODES=0                  # see "Terminal modes" below
TETHER_STATE_DIR=...                    # default ~/.local/state/claude-tether
```

`NO_COLOR` turns colors off on both sides.

## How it works

```
local: tether ── checks: network, Tailscale (optional), ssh login
          │
          └─ ssh -t server tether-attach NAME
                         │
server:  dtach -A ~/.local/state/claude-tether/NAME.sock ── tether-run ── claude --resume ID
```

- **Attach or start.** `dtach -A` attaches to the session's socket, or starts
  `tether-run` if nothing is running.
- **Redraw.** dtach's default redraw is a resize signal, and Claude ignores it
  when the window size hasn't changed, so you would see a blank screen.
  tether-attach uses `-r ctrl_l` instead, which makes Claude redraw every time.
- **Terminal modes.** Claude turns on some terminal modes once, at startup:
  focus events, color-scheme reports, the kitty keyboard protocol and
  modifyOtherKeys. A terminal that attaches later never got them, so
  `tether-attach` sends them again on reattach. In the other direction,
  `tether` turns them off locally whenever ssh ends; otherwise a dropped link
  leaves your shell with garbled keys and a dead Ctrl-C.
- **The same conversation.** Claude keeps
  `~/.claude/sessions/<pid>.json` up to date with its current session id,
  which changes on `/resume` and `/clear`. `tether-run` copies it to
  `NAME.session`, so a restart runs `claude --resume <that id>`. If that
  conversation is gone, or the resume fails, it starts a fresh one. It
  deliberately doesn't use `--continue`, which picks the newest conversation in
  the directory, and that one may belong to a different session.
- **Dropped links.** ssh runs with `ServerAliveInterval=2`,
  `ServerAliveCountMax=4`. Exit code 255 means the connection was lost, so
  `tether` retries with backoff (2, 4, 6, 8, then every 10 s). It tells "this
  machine is offline" apart from "host unreachable". If the login needs a
  password, it lets ssh ask.

## Troubleshooting

- **Blank screen after attaching:** press Ctrl-L. If it happens every time,
  check that the server runs this version (`-r ctrl_l`).
- **Odd keys (for example Shift+Enter) after reattaching from another
  terminal:** the restored modes assume a terminal like the one Claude started
  in. Set `TETHER_RESTORE_MODES=0` on the server, or start the session from the
  terminal you normally use.
- **Is it running?** `ssh myserver 'pgrep -af "dtach -A"'`. To end a session,
  exit Claude, or kill that dtach process.
- **Two windows on one session** work. Both see the same screen, and the
  most recent one sets the size.

## Development

```sh
python3 test/test_server.py   # needs dtach; uses test/fake-claude, no account or API calls
python3 test/test_client.py   # fake ssh; TEST_BASH=/bin/bash to use bash 3.2 on macOS
shellcheck -x bin/* install.sh
```

The tests drive the scripts in a pseudo-terminal. CI runs them on Linux and on
macOS, including the stock bash 3.2.

## License

MIT
