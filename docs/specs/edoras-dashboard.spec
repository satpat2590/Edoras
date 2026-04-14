# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/home/satyamini/edoras/dashboard.py'],
    pathex=[],
    binaries=[],
    datas=[('config.py', '.')],
    hiddenimports=['sqlite3', 'PIL', 'rich', 'numpy', 'pandas', 'yfinance', 'markdown_it', 'pygments'],
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
    a.binaries,
    a.datas,
    [],
    name='edoras-dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
