#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Builds dist/The-Order-1886-x86_64.AppImage from a released Linux package tree.
#
# Usage: bash packaging/linux/appimage.sh --tree "<extracted>/The Order 1886" [options]
#   --tree DIR          the package tree (the folder that holds package-manifest.json); required
#   --out DIR           where the image and its .sha256 go (default: dist)
#   --python DIR        an unpacked python-build-standalone "install_only" tree (python/ inside)
#   --appimagetool F    appimagetool-x86_64.AppImage
#   --runtime F         the type2 runtime
#
# What goes in: the package tree as it ships, the launcher (launcher/order1886_launcher.py), and a
# standalone Python 3.13 with Tk rebuilt for Xft, so the window opens on a system that has no
# GTK, no WebKitGTK and no Python of its own. The host provides glibc, X11, the Vulkan loader and
# the GPU's Vulkan driver, as for a native game.
#
# Downloads are pinned: each is checked against its SHA-256 before use, and a cached copy that
# does not match is downloaded again.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
root=$PWD
tree='' out=dist python_dir='' tool='' runtime=''
while (( $# )); do
    case $1 in
        --tree) tree=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        --python) python_dir=$2; shift 2 ;;
        --appimagetool) tool=$2; shift 2 ;;
        --runtime) runtime=$2; shift 2 ;;
        --help|-h) sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option $1" >&2; exit 2 ;;
    esac
done
[[ -n $tree ]] || { echo 'Missing --tree: the extracted package folder (with package-manifest.json)' >&2; exit 2; }
tree=$(cd -- "$tree" && pwd)
for needed in package-manifest.json bin/order1886-worker/order1886-worker; do
    [[ -e $tree/$needed ]] || { echo "not a package tree: $needed is missing from $tree" >&2; exit 1; }
done
[[ -f launcher/order1886_launcher.py ]] || { echo 'launcher/order1886_launcher.py is missing' >&2; exit 1; }

# ---- Pinned downloads (version, URL, SHA-256) ------------------------------------------------
# To update one: change the version and URL, download the file, put its sha256sum here.
APPIMAGETOOL_VERSION=1.9.1
APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-x86_64.AppImage
APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
RUNTIME_VERSION=20251108
RUNTIME_URL=https://github.com/AppImage/type2-runtime/releases/download/$RUNTIME_VERSION/runtime-x86_64
RUNTIME_SHA256=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
# Python 3.13.16 with Tk 9.0.4 (build-tk-xft.sh pins the matching Tcl/Tk sources).
PYTHON_RELEASE=20261013
PYTHON_VERSION=3.13.16
PYTHON_URL=https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_RELEASE/cpython-$PYTHON_VERSION+$PYTHON_RELEASE-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
PYTHON_SHA256=d0080766b6b74aeb81e1949ef0d9a20e6c17f7b160fb295a69b936ccb17660be

tools=$root/out/appimage-tools
mkdir -p "$tools"
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }
# fetch URL SHA256 FILE: FILE when it already has that hash, else downloaded and checked.
fetch() {
    local url=$1 want=$2 file=$3 got
    if [[ -s $file && $(sha256_of "$file") == "$want" ]]; then return 0; fi
    [[ -e $file ]] && echo "Cached $(basename "$file") does not match its pin; downloading it again."
    rm -f "$file" "$file.part"
    curl -fsSL -o "$file.part" "$url" || { echo "Download failed: $url" >&2; rm -f "$file.part"; exit 1; }
    got=$(sha256_of "$file.part")
    if [[ $got != "$want" ]]; then
        rm -f "$file.part"
        echo "SHA-256 mismatch for $url" >&2
        echo "  expected $want" >&2
        echo "  got      $got" >&2
        exit 1
    fi
    mv "$file.part" "$file"
}
check_local() {
    local got
    got=$(sha256_of "$1")
    [[ $got == "$2" ]] || echo "Note: $3 $1 is not the pinned build (sha256 $got)."
}
if [[ -z $tool ]]; then
    tool=$tools/appimagetool-$APPIMAGETOOL_VERSION
    fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$tool"
    chmod +x "$tool"
else
    check_local "$tool" "$APPIMAGETOOL_SHA256" appimagetool
fi
if [[ -z $runtime ]]; then
    runtime=$tools/runtime-x86_64-$RUNTIME_VERSION
    fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$runtime"
else
    check_local "$runtime" "$RUNTIME_SHA256" runtime
fi
if [[ -z $python_dir ]]; then
    python_dir=$tools/python-$PYTHON_VERSION-$PYTHON_RELEASE
    archive=$tools/python-$PYTHON_VERSION-$PYTHON_RELEASE.tar.gz
    fetch "$PYTHON_URL" "$PYTHON_SHA256" "$archive"
    if [[ ! -x $python_dir/python/bin/python3 ]]; then
        rm -rf "$python_dir" && mkdir -p "$python_dir" && tar -C "$python_dir" -xzf "$archive"
    fi
fi
py=$python_dir/python
[[ -x $py/bin/python3 ]] || { echo "$python_dir is not an unpacked 'install_only' tree (python/)" >&2; exit 1; }
# The one import the launcher makes, checked here rather than in the player's window.
"$py/bin/python3" -I -c 'import tkinter, tkinter.ttk, tkinter.font, tkinter.filedialog' \
    || { echo "$py: Tk is unusable for the launcher (above)" >&2; exit 1; }

patchelf=${PATCHELF:-$(command -v patchelf || true)}
[[ -n $patchelf ]] || { echo 'patchelf is needed (apt install patchelf)' >&2; exit 1; }

app=$root/out/Order1886.AppDir
rm -rf "$app"
share=$app/usr/order1886                     # the package tree (--project-root)
lib=$app/usr/lib
pylib=$app/usr/python/lib
mkdir -p "$share" "$lib/compat" "$app/usr/share/applications" \
    "$app/usr/share/icons/hicolor/256x256/apps" "$app/usr/share/metainfo"

# The package: runtime, worker, gpu/, scripts, docs. Its own folder keeps the layout the worker
# and the launcher expect (user/ never ships, and is created beside the image or in XDG data).
echo 'Package:'
cp -a "$tree/." "$share/"
rm -rf "$share/user"
install -Dm644 launcher/order1886_launcher.py "$share/launcher/order1886_launcher.py"
echo "  $(find "$share" -type f | wc -l) files"

# Python (the launcher) and its text stack.
cp -a "$py" "$app/usr/python"
rm -rf "$app/usr/python/lib/python3"*/test "$app/usr/python/lib/python3"*/idlelib/idle_test \
    "$app/usr/python/lib/python3"*/ensurepip "$app/usr/python/lib/python3"*/lib2to3 2>/dev/null || true
if ! ldd "$pylib"/libtcl*tk*.so 2>/dev/null | grep -q libXft; then
    PATCHELF=$patchelf bash packaging/linux/build-tk-xft.sh "$app/usr/python" "$root/$tools/tk-xft"
fi
rm -f "$pylib"/libtcl*tk*.so.orig

# What that Tk loads: Xft needs fontconfig and freetype, and those pull libexpat, libpng, libbz2
# and libharfbuzz. A desktop has them, a bare X server does not, and `import tkinter` then fails
# before the window exists. They go next to the Tk library, which finds them through $ORIGIN;
# X11 and zlib stay the host's, as for the game.
host_lib() {
    case $1 in
        ld-linux*|libc.so*|libm.so*|libdl.so*|libpthread.so*|librt.so*|libresolv.so*|libutil.so*) return 0 ;;
        libstdc++.so*|libgcc_s.so*) return 0 ;;
        libX11.so*|libX11-xcb.so*|libxcb*.so*|libXau.so*|libXdmcp.so*|libXext.so*|libz.so*) return 0 ;;
        libbsd.so*|libmd.so*) return 0 ;;
        libGL*|libEGL*|libdrm*|libnvidia*|libvulkan*|libgbm*) return 0 ;;
        libwayland-*.so*) return 0 ;;
    esac
    return 1
}
collect_launcher_libs() {
    local elf=$1 name path
    while read -r name _ path _; do
        [[ $path == /* ]] || continue
        host_lib "$name" && continue
        [[ -e $pylib/$name ]] && continue
        cp -L "$path" "$pylib/$name"
        chmod 644 "$pylib/$name"
        "$patchelf" --set-rpath '$ORIGIN' "$pylib/$name" 2>/dev/null || true
        echo "  bundled $name (launcher)"
        collect_launcher_libs "$pylib/$name"
    done < <(ldd "$elf" | grep '=>')
}
for elf in "$pylib"/libtcl*tk*.so "$pylib"/libXft.so.2 "$pylib"/libpython3*.so* \
           "$app"/usr/python/lib/python3*/lib-dynload/_tkinter*.so; do
    [[ -e $elf ]] || continue                 # a glob that matched nothing stays literal
    collect_launcher_libs "$elf"
done
# Bytecode inside the image: a read-only AppImage cannot write it at run time.
"$app/usr/python/bin/python3" -I -m compileall -q -j0 "$app/usr/python/lib" \
    "$share/launcher/order1886_launcher.py" >/dev/null 2>&1 || true

# The newest GLIBC symbol version the image needs from the host (AppRun says so plainly when the
# host's is older). The package's own binaries are covered by the release it came from.
newest() { # PREFIX FILE...: the newest symbol version PREFIX_x.y the files import
    local prefix=$1 f; shift
    for f in "$@"; do
        [[ -f $f ]] && file -b "$f" | grep -q ELF && objdump -T "$f" 2>/dev/null || true
    done | grep -o "${prefix}_[0-9.]*" | sort -V | tail -1 || true
}
mapfile -t python_elves < <(find "$app/usr/python" -type f \( -name '*.so*' -o -path '*/bin/python3*' \))
glibc=$(newest GLIBC "${python_elves[@]}")
echo "${glibc#GLIBC_}" > "$lib/compat/glibc"
echo "Python and launcher need glibc ${glibc#GLIBC_}+ from the host"

# Desktop integration.
install -m755 packaging/linux/AppRun "$app/AppRun"
icon=$root/docs/images/divinusx-avatar.png
[[ -f $icon ]] || { echo "the icon is missing: $icon" >&2; exit 1; }
cp "$icon" "$app/usr/share/icons/hicolor/256x256/apps/the-order-1886.png"
cp "$icon" "$app/the-order-1886.png"
cp packaging/linux/the-order-1886.desktop "$app/usr/share/applications/the-order-1886.desktop"
cp packaging/linux/the-order-1886.desktop "$app/"
cat > "$app/usr/share/metainfo/the-order-1886.appdata.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>the-order-1886</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>GPL-2.0-or-later</project_license>
  <name>The Order: 1886</name>
  <summary>Launcher and Vulkan runtime for The Order: 1886 on Linux; bring your own game files</summary>
  <description><p>Runs The Order: 1886 from your own copy of the game. No game files included.</p></description>
  <launchable type="desktop-id">the-order-1886.desktop</launchable>
  <content_rating type="oars-1.1"/>
</component>
EOF

mkdir -p "$root/$out"
image=$root/$out/The-Order-1886-x86_64.AppImage
rm -f "$image" "$image.sha256"
# No FUSE needed to build: the tool runs from its extracted copy.
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream --runtime-file "$runtime" \
    "$app" "$image" >"$root/out/appimagetool.log" 2>&1 \
    || { tail -20 "$root/out/appimagetool.log" >&2; exit 1; }
(cd "$root/$out" && sha256sum "$(basename "$image")" | sed 's/  / */' > "$image.sha256")
ls -lh "$image"
