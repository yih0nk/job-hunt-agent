# PyInstaller spec for the bundled backend. `npm run build:backend` in desktop/ runs it.
# Output: desktop/build/backend/jobhunt-backend/ (copied into the app by electron-builder).
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "jobhunt", "templates"), "jobhunt/templates"),
         (os.path.join(ROOT, "web", "dist"), "web/dist")]
binaries, hiddenimports = [], []
for pkg in ("typst", "uvicorn", "anthropic", "keyring"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("jobhunt")

a = Analysis([os.path.join(SPECPATH, "backend_entry.py")], pathex=[ROOT], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jobhunt-backend", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="jobhunt-backend")
