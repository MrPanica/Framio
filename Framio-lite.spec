from pathlib import Path

import PyQt6


ROOT = Path(SPECPATH).resolve()
qt_bin = Path(PyQt6.__file__).parent / "Qt6" / "bin"
extra_binaries = []
for pattern in ("MSVCP140*.dll", "VCRUNTIME140*.dll"):
    extra_binaries.extend((str(path), ".") for path in qt_bin.glob(pattern))


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=extra_binaries,
    datas=[(str(ROOT / "icon.ico"), "."), (str(ROOT / "icon.png"), ".")],
    hiddenimports=[
        "requests", "cv2", "numpy", "PIL", "PyQt6.QtSvg",
        "pyaudiowpatch", "_portaudiowpatch", "keyboard", "mss"
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # imageio-ffmpeg is deliberately omitted from this artifact. Runtime
    # recorders already fall back to OpenCV/Pillow when FFmpeg is unavailable.
    excludes=[
        "imageio_ffmpeg", "imageio", "tkinter", "_tkinter",
        "cryptography", "bcrypt", "PIL._avif",
        "PyQt6.QtPdf", "PyQt6.QtPdfWidgets",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebChannel",
        "PyQt6.Qt3DCore", "PyQt6.Qt3DGui", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic",
        "PyQt6.Qt3DRender", "PyQt6.Qt3DExtras", "PyQt6.Qt3DAnimation",
    ],
    noarchive=False,
    optimize=0,
)

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
    a.binaries,
    a.datas,
    [],
    name="Framio",
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
    icon=[str(ROOT / "icon.ico")],
)
