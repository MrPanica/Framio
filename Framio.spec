import sys
from pathlib import Path
import PyQt6
import imageio_ffmpeg

qt_bin = Path(PyQt6.__file__).parent / "Qt6" / "bin"
extra_binaries = [(str(p), ".") for p in qt_bin.glob("MSVCP140*.dll")]

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
        'requests', 'cv2', 'numpy', 'PIL', 'imageio', 'imageio_ffmpeg',
        'PyQt6.QtSvg', 'pyaudiowpatch', '_portaudiowpatch', 'keyboard', 'mss'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
