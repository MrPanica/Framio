import sys
from pathlib import Path
import PyQt6
import imageio_ffmpeg

qt_bin = Path(PyQt6.__file__).parent / "Qt6" / "bin"
extra_binaries = []
for pattern in ("MSVCP140*.dll", "VCRUNTIME140*.dll"):
    extra_binaries.extend((str(p), ".") for p in qt_bin.glob(pattern))

ffmpeg_dir = Path(imageio_ffmpeg.__file__).parent / "binaries"
if ffmpeg_dir.exists():
    for exe in ffmpeg_dir.glob("*.exe"):
        extra_binaries.append((str(exe), "imageio_ffmpeg/binaries"))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=extra_binaries,
    datas=[('icon.ico', '.'), ('icon.png', '.')],
    hiddenimports=[
        'requests', 'cv2', 'numpy', 'PIL', 'imageio_ffmpeg',
        'PyQt6.QtSvg', 'pyaudiowpatch', '_portaudiowpatch', 'keyboard', 'mss'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', '_tkinter', 'imageio', 'cryptography', 'bcrypt',
        'PIL._avif', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebChannel',
        'PyQt6.Qt3DCore', 'PyQt6.Qt3DGui', 'PyQt6.Qt3DInput', 'PyQt6.Qt3DLogic',
        'PyQt6.Qt3DRender', 'PyQt6.Qt3DExtras', 'PyQt6.Qt3DAnimation',
    ],
    noarchive=False,
    optimize=0,
)

# Qt6Core imports the unversioned Windows ICU DLL. PyInstaller may resolve it
# to the bundled Poppler ICU from the build environment, which is incompatible
# with Qt and causes QtCore.pyd to fail during startup.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {
        # These are pulled in by optional Qt/PDF support from the build
        # environment. Framio does not import QtPdf or use Poppler.
        "icuuc.dll",
        "icudt78.dll",
        "qt6pdf.dll",
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        # Framio uses Qt's raster widgets; no QOpenGLWidget or Qt Quick
        # surface is used, so the 20 MiB software OpenGL fallback is not
        # needed in the portable distribution.
        "opengl32sw.dll",
    }
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Framio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Framio',
)
