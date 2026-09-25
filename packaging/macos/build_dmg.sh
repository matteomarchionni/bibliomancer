#!/bin/sh
# macOS: .app (PyInstaller) -> drag-to-Applications .dmg. Same script in both apps.
#
#   sh packaging/macos/build_dmg.sh            (from the repository root, on a Mac)
#
# Unsigned builds: the first launch needs right-click > Open (Gatekeeper).
# With an Apple Developer ID set MAC_SIGN_ID and the app is signed here;
# notarisation (xcrun notarytool) is a separate step in the release workflow.
set -e
APP_NAME=$(sed -n 's/^APP_NAME = "\(.*\)"/\1/p' packaging/app.spec)
FILE_NAME=$(echo "$APP_NAME" | tr -d ' ')
VERSION=${APP_VERSION:-2.0.0}
ARCH=$(uname -m)

python packaging/make_icons.py
pyinstaller packaging/app.spec --noconfirm

if [ -n "$MAC_SIGN_ID" ]; then
    codesign --deep --force --options runtime --sign "$MAC_SIGN_ID" "dist/$APP_NAME.app"
fi

rm -rf build/dmg && mkdir -p build/dmg
cp -R "dist/$APP_NAME.app" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "$APP_NAME" -srcfolder build/dmg -ov -format UDZO \
    "dist/$FILE_NAME-$VERSION-macos-$ARCH.dmg"
echo "dist/$FILE_NAME-$VERSION-macos-$ARCH.dmg"
