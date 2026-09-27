#!/usr/bin/env bash
# Install subproto onto your PATH without a package index.
#
# The shim it writes pins an interpreter that is >= 3.9 rather than a bare `python3`:
# on a stock macOS or CentOS 7, `python3` exists and is 3.6, which fails at import
# time with a SyntaxError that looks like a bug in this project. Set PYTHON=... to
# choose the interpreter yourself.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${SUBPROTO_BIN_DIR:-$HOME/.local/bin}"

ok_version() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  >/dev/null 2>&1; }

PY="${PYTHON:-python3}"
if ! ok_version "$PY"; then
  picked=""
  for cand in python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cand" >/dev/null 2>&1 && ok_version "$cand"; then
      picked="$cand"
      break
    fi
  done
  if [ -z "$picked" ]; then
    echo "subproto needs Python 3.9 or newer." >&2
    echo "  'python3' here is: $("$PY" --version 2>&1 || echo 'not installed')" >&2
    echo "Install a newer interpreter, or re-run with the one you want:" >&2
    echo "  PYTHON=/path/to/python3.11 bash install.sh" >&2
    exit 1
  fi
  PY="$picked"
fi

mkdir -p "$BIN"
cat > "$BIN/subproto" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$HERE:\${PYTHONPATH:-}"
exec "$PY" -m subproto "\$@"
EOF
chmod +x "$BIN/subproto"
echo "installed subproto -> $BIN/subproto   (interpreter: $PY)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "note: add it to PATH ->  export PATH=\"$BIN:\$PATH\"" ;;
esac
"$BIN/subproto" --help >/dev/null && echo "ok"
