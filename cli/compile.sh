#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f main.go ]; then
    echo "✗ main.go not found in $SCRIPT_DIR"
    echo "  Place this script next to main.go (and go.mod)."
    exit 1
fi

if ! command -v go >/dev/null 2>&1; then
    echo "✗ Go is not installed. Get it at https://go.dev/dl"
    exit 1
fi
echo "Go: $(go version)"

if [ ! -f go.mod ]; then
    echo "→ No go.mod, initializing module..."
    go mod init hoplita-cli
    go get gopkg.in/ini.v1
fi
go mod tidy

mkdir -p dist

build_linux() {
    echo "→ Building Linux (amd64)..."
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o dist/hoplita-cli .
    echo "  ✓ dist/hoplita-cli ($(du -h dist/hoplita-cli | cut -f1))"
}

build_windows() {
    echo "→ Building Windows (amd64)..."
    CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o dist/hoplita.exe .
    echo "  ✓ dist/hoplita.exe ($(du -h dist/hoplita.exe | cut -f1))"
}

TARGET="${1:-all}"
case "$TARGET" in
    linux)   build_linux ;;
    windows) build_windows ;;
    all)     build_linux; build_windows ;;
    *)
        echo "Usage: $0 [linux|windows|all]"
        exit 1
        ;;
esac

echo ""
echo "Binaries in: $SCRIPT_DIR/dist/"
ls -la dist/
echo ""
echo "Windows: bundle dist/hoplita.exe with install.py (it installs it under %APPDATA%\\hoplita\\hoplita.exe)"
echo "Linux:   use dist/hoplita-cli directly"
