#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Opens the launcher from the AppImage on a virtual display and checks what it built, then that it
# stays open: a missing glibc version, a library the bundle forgot or an unusable Tk stops it at
# import. Run as root in a fresh container of the distribution to check; it installs Xvfb and a
# font, as a desktop has them (.github/workflows/linux-appimage.yml).
# Usage: smoke-test.sh path/to/The-Order-1886-x86_64.AppImage
set -euo pipefail
[[ $# == 1 ]] || { echo "Usage: $0 path/to/The-Order-1886-x86_64.AppImage" >&2; exit 2; }
appimage=$(readlink -f -- "$1")
# What a desktop has and a bare container does not: an X server, the X client stack, fontconfig
# (its configuration and fonts are the host's; the library the image carries is used regardless,
# through RUNPATH) and a font. The image brings its own Python, Tk, Xft, fontconfig and freetype.
if command -v apt-get >/dev/null; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        xvfb fontconfig fonts-dejavu-core \
        libx11-6 libxext6 libxrender1 libxi6 libxfixes3 libxrandr2 libxcursor1 >/dev/null
elif command -v dnf >/dev/null; then
    dnf install -y -q xorg-x11-server-Xvfb fontconfig dejavu-sans-fonts \
        libX11 libXext libXrender libXi libXfixes libXrandr libXcursor >/dev/null
elif command -v zypper >/dev/null; then
    zypper -nq install xorg-x11-server-Xvfb fontconfig dejavu-fonts \
        libX11-6 libXext6 libXrender1 libXi6 libxfixes3 libXrandr2 libXcursor1 >/dev/null
elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm --needed xorg-server-xvfb fontconfig ttf-dejavu \
        libx11 libxext libxrender libxi libxfixes libxrandr libxcursor >/dev/null
fi
grep -m1 PRETTY_NAME /etc/os-release
ldd --version | sed -n 1p  # head would end ldd with SIGPIPE (pipefail)
work=$(mktemp -d)
cd "$work"
"$appimage" --appimage-extract >/dev/null
# The launcher's text stack ships with it: without it, a system that is not a full desktop stops
# at `import tkinter` with "libfontconfig.so.1: cannot open shared object file".
for lib in squashfs-root/usr/python/lib/libtcl*tk*.so squashfs-root/usr/python/lib/libXft.so.2 \
           squashfs-root/usr/python/lib/libfontconfig.so.1 \
           squashfs-root/usr/python/lib/libfreetype.so.6 \
           squashfs-root/usr/python/lib/libexpat.so.1 \
           squashfs-root/usr/order1886/launcher/order1886_launcher.py \
           squashfs-root/usr/order1886/bin/order1886-worker/order1886-worker; do
    if [[ ! -e $lib ]]; then
        echo "FAIL: the image has no ${lib#squashfs-root/}" >&2
        exit 1
    fi
done
if [[ -e squashfs-root/usr/order1886/bin/order1886-launcher ]]; then
    echo 'Note: the package still ships its WebKitGTK window; the image does not use it.'
fi
# The shell entry point works without a display: its options must be there and be documented.
help=$(squashfs-root/AppRun --help)
for option in --direct-play --game-dir; do
    if ! grep -q -- "$option" <<<"$help"; then
        echo "FAIL: AppRun --help does not list $option" >&2
        echo "$help" >&2
        exit 1
    fi
done
# --direct-play without a game folder is refused with instructions, not a Python traceback.
status=0
HOME=$work squashfs-root/AppRun --direct-play > run.log 2>&1 || status=$?
if [[ $status != 1 ]] || ! grep -q 'Direct play needs setup' run.log; then
    cat run.log >&2
    echo "FAIL: AppRun --direct-play on a missing game exited $status" >&2
    exit 1
fi
Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &
export DISPLAY=:99 HOME=$work
sleep 2
# --self-test builds the window from the worker's catalogue and reports it: this is the launcher
# asked for every page and field, with no display interaction.
status=0
timeout 60 squashfs-root/AppRun --self-test > self.log 2>&1 || status=$?
cat self.log
if [[ $status != 0 ]] || ! grep -qE 'self-test: [1-9][0-9]* pages, [1-9][0-9]* rows' self.log; then
    find squashfs-root -name '_tkinter*.so' -exec ldd {} + 2>/dev/null | grep 'not found' || true
    echo "FAIL: the launcher did not build its pages ($status)" >&2
    exit 1
fi
# And the launcher itself stays open.
status=0
timeout 20 squashfs-root/AppRun > launcher.log 2>&1 || status=$?
cat launcher.log
if [[ $status != 124 ]] || grep -qE 'GLIBC_|ImportError|Traceback' launcher.log; then
    find squashfs-root -name '_tkinter*.so' -exec ldd {} + 2>/dev/null | grep 'not found' || true
    echo "FAIL: the launcher exited ($status) instead of staying open" >&2
    exit 1
fi
echo 'OK: the launcher built its pages and stayed open'
