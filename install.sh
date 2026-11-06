#!/bin/sh
# claude-tether installer.
#   ./install.sh client          install `tether` on this machine (the one you type on)
#   ./install.sh server          install the server side on this machine
#   ./install.sh server HOST     install the server side on HOST over ssh
#   ./install.sh uninstall       remove whatever is installed on this machine
# PREFIX (default ~/.local) sets where files go; scripts land in $PREFIX/bin.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
prefix=${PREFIX:-$HOME/.local}
bindir=$prefix/bin

say() { printf '%s\n' "$*"; }
path_hint() {
  case ":$PATH:" in *":$bindir:"*) ;; *) say "note: $bindir is not on your PATH; add it to your shell profile" ;; esac
}

case ${1:-} in
  client)
    mkdir -p "$bindir"
    install -m 755 "$here/bin/tether" "$bindir/tether"
    say "installed $bindir/tether"
    path_hint
    say "next: tether HOST   (or set TETHER_HOST in ~/.config/claude-tether/config)"
    ;;
  server)
    if [ -n "${2:-}" ]; then
      host=$2
      say "installing the server side on $host…"
      # shellcheck disable=SC2029  # $prefix is meant to expand here
      ssh "$host" "mkdir -p ~/.local/bin" &&
        scp -q "$here/bin/tether-attach" "$here/bin/tether-run" "$host:.local/bin/" &&
        ssh "$host" 'chmod 755 ~/.local/bin/tether-attach ~/.local/bin/tether-run
          command -v dtach >/dev/null || echo "warning: dtach is missing on $(hostname): install it (apt/dnf/pacman/brew install dtach)"
          { command -v claude || [ -x ~/.local/bin/claude ]; } >/dev/null || echo "warning: claude not found on $(hostname)"
          echo "installed ~/.local/bin/tether-attach and tether-run on $(hostname)"'
      exit
    fi
    mkdir -p "$bindir"
    install -m 755 "$here/bin/tether-attach" "$bindir/tether-attach"
    install -m 755 "$here/bin/tether-run" "$bindir/tether-run"
    say "installed $bindir/tether-attach and $bindir/tether-run"
    command -v dtach >/dev/null || say "warning: dtach is missing: install it (apt/dnf/pacman/brew install dtach)"
    { command -v claude || [ -x "$HOME/.local/bin/claude" ]; } >/dev/null || say "warning: claude not found"
    [ "$bindir" = "$HOME/.local/bin" ] || say "note: clients must set TETHER_REMOTE=$bindir/tether-attach"
    ;;
  uninstall)
    for f in tether tether-attach tether-run; do
      [ -e "$bindir/$f" ] && rm -f "$bindir/$f" && say "removed $bindir/$f"
    done
    say "state and sockets (if any) are in ${XDG_STATE_HOME:-$HOME/.local/state}/claude-tether"
    ;;
  *)
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
