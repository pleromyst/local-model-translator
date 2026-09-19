# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


rapidocr_data = collect_data_files("rapidocr")

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("config/settings.json", "config"),
        ("assets/locallens.ico", "assets"),
        *rapidocr_data,
    ],
    hiddenimports=[
        "rapidocr.main",
        "rapidocr.inference_engine.onnxruntime",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LocalLens",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/locallens.ico",
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)
distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LocalLens",
)
