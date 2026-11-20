#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Build dependencies of the release AppImage on Ubuntu 24.04
# (.github/workflows/linux-appimage.yml). Its glibc (2.39) is the oldest one players can have:
# SteamOS 3.6+, Ubuntu 24.04+, Debian 13, Fedora 40+.
# The port itself is not built here: the engine and runtime come from the released package, so
# this only needs what wraps the launcher around it. Xft/fontconfig/freetype are the development
# files packaging/linux/build-tk-xft.sh rebuilds Tk against.
set -euo pipefail
sudo=$([[ $(id -u) == 0 ]] || echo sudo)
export DEBIAN_FRONTEND=noninteractive
$sudo apt-get update
$sudo apt-get install -y --no-install-recommends \
    build-essential make pkg-config git curl ca-certificates file xz-utils \
    patchelf binutils \
    libx11-dev libxext-dev libxrender-dev libxft-dev \
    libfontconfig1-dev libfreetype-dev
