#!/usr/bin/env bash
# Install subproto onto your PATH without a package index.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${SUBPROTO_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"
cat > "$BIN/subproto" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$HERE:\${PYTHONPATH:-}"
exec python3 -m subproto "\$@"
EOF
chmod +x "$BIN/subproto"
echo "installed subproto -> $BIN/subproto"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "note: add it to PATH ->  export PATH=\"$BIN:\$PATH\"" ;;
esac
"$BIN/subproto" --help >/dev/null && echo "ok"
