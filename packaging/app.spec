# PyInstaller recipe - same structure in both apps (only the names at the top differ).
#
#   pyinstaller packaging/app.spec --noconfirm       (from the repository root)
#
# Output: dist/<APP_NAME>/ with two executables sharing one set of libraries:
#   <PKG>      windowed: the panel, and what the OS scheduler starts
#   <PKG>-cli  console:  the jobs the panel runs and reads (python -m <PKG> ...)
# On macOS also dist/<APP_NAME>.app. The installers (Inno Setup, .dmg,
# AppImage) are built from this folder - see packaging/README.md.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

PKG = "bibliomancer"
APP_NAME = "BiblioMancer"
ICON = "bibliomancer_icon"
BUNDLE_ID = "local.bibliomancer"

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 - defined by PyInstaller
ASSETS = os.path.join(ROOT, "src", PKG, "assets")
icon = os.path.join(ASSETS, f"{ICON}.ico") if sys.platform.startswith("win") else (
    os.path.join(ROOT, "build", f"{ICON}.icns") if sys.platform == "darwin" else None)

a = Analysis(  # noqa: F821
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    pathex=[os.path.join(ROOT, "src")],
    datas=[(ASSETS, f"{PKG}/assets")],
    hiddenimports=collect_submodules(PKG),
    excludes=["matplotlib", "pandas", "IPython", "notebook", "pytest", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name=PKG, console=False,  # noqa: F821
              icon=icon, upx=False)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name=f"{PKG}-cli",  # noqa: F821
              console=True, icon=icon, upx=False)
coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{APP_NAME}.app", icon=icon, bundle_identifier=BUNDLE_ID,  # noqa: F821
                 info_plist={"CFBundleShortVersionString": os.environ.get("APP_VERSION", "2.0.0"),
                             "NSHighResolutionCapable": True,
                             "LSApplicationCategoryType": "public.app-category.productivity"})
