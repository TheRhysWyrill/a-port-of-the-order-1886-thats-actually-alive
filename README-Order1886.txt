THE ORDER: 1886 FOR PC - COMPATIBILITY RUNTIME AND LAUNCHER

Read QUICKSTART.txt first: setup, the required game-folder structure, the
launcher pages, controls, saves, updates and troubleshooting, in English and
Brazilian Portuguese.

On Windows, extract the entire archive to a writable folder and open
The Order 1886.exe; Play The Order 1886.exe starts the game directly with the
saved configuration. Keep both EXEs and the bin folder together.

On Linux the launcher and the runtime are one file,
The-Order-1886-x86_64.AppImage: make it executable and run it, choose your own
extracted game folder (CUSA00076, CUSA00035, CUSA00070, CUSA00100, CUSA00670
or CUSA00785) on Game & Content, then press Play. Game & Content can create a
desktop shortcut or add it to Steam. The launcher is Tkinter and the image
carries its own Python, so no WebKitGTK, no GTK and no Python have to be
installed. --direct-play starts the game with the saved settings and no
window, and --game-dir sets the folder the first time. The plain package
(order1886-linux-x86_64-*.tar.gz) holds the same runtime, and there
"Play The Order 1886" plays directly. No compiler is required either way, and
game content is not included.

This is emulator-backed compatibility software for The Order: 1886, not an
official source-native PC port. It runs the original PS4 executable with a
dedicated runtime and a Vulkan renderer derived from shadPS4. Gameplay has
been tested in parts of the campaign; full progression, long sessions and
sustained 4K/60 are not verified. 60 FPS and above are experimental.

Settings, prepared images, logs, caches and saves are kept in user/order1886
beside the launcher (beside the AppImage or the package folder; when that
cannot be written, ~/.local/share/order1886, and ORDER1886_STATE overrides
both). The original game files are never modified. Keep the user folder
when moving or updating the installation.

package-manifest.json records the hash of every shipped file and
bin/runtime-build.json identifies the source revision of the runtime. LICENSE
and the notices folder hold the licenses and upstream attribution; source
attribution is in notices/ATTRIBUTION.txt. Corresponding source must accompany
any public redistribution: https://github.com/JulioCacko/The-Order-1886_pc
Game assets, the launcher artwork and trademarks belong to their owners.
