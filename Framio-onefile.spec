from pathlib import Path

import PyQt6
import imageio_ffmpeg


ROOT = Path(SPECPATH).resolve()
qt_bin = Path(PyQt6.__file__).parent / "Qt6" / "bin"
extra_binaries = []
for pattern in ("MSVCP140*.dll", "VCRUNTIME140*.dll"):
    extra_binaries.extend((str(path), ".") for path in qt_bin.glob(pattern))

ffmpeg_dir = Path(imageio_ffmpeg.__file__).parent / "binaries"
if ffmpeg_dir.exists():
    for exe in ffmpeg_dir.glob("*.exe"):
        extra_binaries.append((str(exe), "imageio_ffmpeg/binaries"))


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=extra_binaries,
    datas=[(str(ROOT / "icon.ico"), "."), (str(ROOT / "icon.png"), ".")],
    hiddenimports=[
        "requests", "cv2", "numpy", "PIL", "imageio_ffmpeg",
        "PyQt6.QtSvg", "pyaudiowpatch", "_portaudiowpatch", "keyboard", "mss"
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "_tkinter", "imageio", "cryptography", "bcrypt",
        "PIL._avif", "PyQt6.QtPdf", "PyQt6.QtPdfWidgets",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebChannel",
        "PyQt6.Qt3DCore", "PyQt6.Qt3DGui", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic",
        "PyQt6.Qt3DRender", "PyQt6.Qt3DExtras", "PyQt6.Qt3DAnimation",
    ],
    noarchive=False,
    optimize=0,
)

# Не допускаем несовместимые ICU DLL в bundle: из-за них QtCore.pyd
# может завершиться с «не найдена указанная процедура».
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

# One-file bootloader распаковывает этот архив во временную папку при запуске,
# запускает приложение и удаляет временные файлы после завершения.
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
