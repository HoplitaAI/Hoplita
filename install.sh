#!/bin/sh

main() {
    set -eu
    REPO_TARBALL="https://github.com/HoplitaAI/Hoplita/archive/refs/heads/main.tar.gz"
    HOPLITA_RERUN="curl -fsSL https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.sh | sh -s --"
    export HOPLITA_RERUN
    UV_VERSION="0.13.6"
    PY_WANT="3.12"

    say() { printf '%s\n' "$*"; }
    die() { printf 'error: %s\n' "$*" >&2; exit 1; }
    have() { command -v "$1" >/dev/null 2>&1; }

    have tar || die "tar is needed"
    if have curl; then
        fetch() { curl -fsSL --retry 2 -o "$2" "$1"; }
    elif have wget; then
        fetch() { wget -q -O "$2" "$1"; }
    else
        die "curl or wget is needed"
    fi

    prefix=""
    uninstall=0
    prev=""
    for a in "$@"; do
        case "$a" in
            --prefix=*) prefix="${a#--prefix=}" ;;
            --uninstall) uninstall=1 ;;
        esac
        [ "$prev" = "--prefix" ] && prefix="$a"
        prev="$a"
    done
    if [ -z "$prefix" ]; then
        prefix="$HOME/hoplita"
        if [ ! -e "$prefix/hoplita-mcp.py" ] && [ -e /opt/hoplita/hoplita-mcp.py ]; then
            prefix="/opt/hoplita"
        fi
    fi
    case "$prefix" in
        "~"/*) prefix="$HOME/${prefix#\~/}" ;;
    esac

    work=$(mktemp -d "${TMPDIR:-/tmp}/hoplita-install.XXXXXX")
    trap 'rm -rf "$work"' EXIT INT TERM

    say "HOPLITA: downloading the source"
    src="${HOPLITA_SOURCE:-$REPO_TARBALL}"
    case "$src" in
        http://*|https://*) fetch "$src" "$work/src.tar.gz" ;;
        *) cp "$src" "$work/src.tar.gz" ;;
    esac
    mkdir "$work/src"
    tar -xzf "$work/src.tar.gz" -C "$work/src"
    srcdir=$(dirname "$(find "$work/src" -maxdepth 2 -name install.py | head -n 1)")
    [ -f "$srcdir/install.py" ] || die "the download holds no install.py"

    usable() {
        "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' \
            >/dev/null 2>&1
    }
    py=""
    for c in "$prefix/venv/bin/python" python3.14 python3.13 python3.12 python3.11 python3; do
        if have "$c" || [ -x "$c" ]; then
            if [ "$c" = "$prefix/venv/bin/python" ]; then
                "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
                    >/dev/null 2>&1 && py="$c" && break
            elif usable "$c"; then
                py=$(command -v "$c")
                break
            fi
        fi
    done

    if [ -z "$py" ]; then
        say "HOPLITA: no Python 3.11+ with venv here; fetching Python $PY_WANT with uv"
        case "$(uname -s)-$(uname -m)" in
            Linux-x86_64) target="x86_64-unknown-linux-gnu" ;;
            Linux-aarch64|Linux-arm64) target="aarch64-unknown-linux-gnu" ;;
            Darwin-x86_64) target="x86_64-apple-darwin" ;;
            Darwin-arm64) target="aarch64-apple-darwin" ;;
            *) die "no standalone Python for $(uname -s) $(uname -m): install Python 3.11+ and run this again" ;;
        esac
        fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz" \
            "$work/uv.tar.gz"
        tar -xzf "$work/uv.tar.gz" -C "$work"
        uv="$work/uv-$target/uv"
        if [ "$uninstall" = 1 ]; then
            pydir="$work/python"
        else
            pydir="$prefix/python"
            mkdir -p "$prefix"
        fi
        UV_PYTHON_INSTALL_DIR="$pydir" UV_CACHE_DIR="$work/uv-cache" \
            "$uv" python install --quiet --no-bin "$PY_WANT"
        py=$(UV_PYTHON_INSTALL_DIR="$pydir" "$uv" python find --managed-python "$PY_WANT")
        [ -x "$py" ] || die "uv could not provide Python $PY_WANT"
    fi
    say "HOPLITA: using $py"

    if [ -t 0 ] || ! (: </dev/tty) 2>/dev/null; then
        "$py" "$srcdir/install.py" "$@"
    else
        "$py" "$srcdir/install.py" "$@" </dev/tty
    fi
}

main "$@"
