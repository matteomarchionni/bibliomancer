# Packaging

Same layout in BiblioMancer and PhotoMancer. Every installer
is built from one PyInstaller folder (`packaging/app.spec`): two executables,
`<pkg>` (windowed: the panel, and what the weekly task starts) and `<pkg>-cli`
(console: the jobs the panel runs), sharing one set of libraries.

| System | Result | Built by | Shortcuts |
|---|---|---|---|
| Windows | `dist/<App>-<version>-Setup.exe` | `packaging/windows/installer.iss` (Inno Setup 6) | installer checkboxes: *desktop icon* (off), *Start menu* (on) |
| macOS | `dist/<App>-<version>-macos-<arch>.dmg` | `packaging/macos/build_dmg.sh` | drag to Applications (Launchpad, Spotlight) |
| Linux | `dist/<App>-<version>-x86_64.AppImage` | `packaging/linux/build_appimage.sh` | first-run window, or `<pkg>-cli shortcuts --menu --desktop` |
| any (Python) | `pipx install .` / `pip install .` | `pyproject.toml` | `<pkg> shortcuts --menu --desktop` |

GitHub Actions builds all of them on every version tag (`.github/workflows/release.yml`)
and attaches them to the release; `tests.yml` runs the tests on the three systems.

## Build locally

```
python -m pip install -e ".[build]"
pyinstaller packaging/app.spec --noconfirm          # dist/<App>/
iscc packaging/windows/installer.iss                  # Windows (Inno Setup 6 installed)
sh packaging/macos/build_dmg.sh                       # macOS
sh packaging/linux/build_appimage.sh                  # Linux
```

Set `APP_VERSION` (e.g. `2.0.1`) to stamp the version into the file names and the installer.

## What the installed app does differently from a source checkout

* Settings in the per-user folder (`%APPDATA%\<App>`, `~/Library/Application Support/<App>`,
  `~/.config/<app>`); data where the first-run window says (default `Documents`).
* The first-run window asks for the folders, checks LM Studio and the model (with a
  download link) and - except after the Windows installer, which already asked -
  offers the desktop icon / menu entry.
* The weekly / monthly task runs `<pkg> scheduled ...` (no console window) and
  writes `logs/run_scheduled.log`, which the panel follows.
* Uninstalling (Windows) removes the scheduled task and the shortcuts; data and
  settings are kept.

## Notes

* macOS builds are unsigned unless `MAC_SIGN_ID` is set: the first launch needs
  right-click > Open. Signing and notarisation need an Apple Developer ID.
* The Windows installer is not code-signed: SmartScreen may show "unknown publisher"
  on first run (More info > Run anyway) until a signing certificate is used.
* LM Studio is not bundled; the first-run window links to https://lmstudio.ai.
