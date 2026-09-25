#!/bin/sh
# Linux: PyInstaller folder -> one-file AppImage that runs on most distributions.
# Same script in both apps.
#
#   sh packaging/linux/build_appimage.sh       (from the repository root)
#
# Needs appimagetool (downloaded here if missing) and FUSE to run AppImages.
# The app offers the menu entry / desktop icon itself on first run (setup wizard)
# or with `<pkg>-cli shortcuts --menu --desktop`.
set -e
PKG=$(sed -n 's/^PKG = "\(.*\)"/\1/p' packaging/app.spec)
APP_NAME=$(sed -n 's/^APP_NAME = "\(.*\)"/\1/p' packaging/app.spec)
ICON=$(sed -n 's/^ICON = "\(.*\)"/\1/p' packaging/app.spec)
FILE_NAME=$(echo "$APP_NAME" | tr -d ' ')
VERSION=${APP_VERSION:-2.0.0}

python packaging/make_icons.py
pyinstaller packaging/app.spec --noconfirm

APPDIR=build/AppDir
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/bin"
cp -R "dist/$APP_NAME/." "$APPDIR/usr/bin/"
cp "build/$ICON.png" "$APPDIR/$PKG.png"
cat > "$APPDIR/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Exec=$PKG
Icon=$PKG
Categories=Utility;Science;
Terminal=false
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
EOF
echo "exec \"\$HERE/usr/bin/$PKG\" \"\$@\"" >> "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

if ! command -v appimagetool >/dev/null 2>&1; then
    wget -q -O build/appimagetool \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x build/appimagetool
    TOOL=build/appimagetool
else
    TOOL=appimagetool
fi
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "dist/$FILE_NAME-$VERSION-x86_64.AppImage"
echo "dist/$FILE_NAME-$VERSION-x86_64.AppImage"
