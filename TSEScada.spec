# -*- mode: python ; coding: utf-8 -*-
import sys
# --- THE FIX IS HERE ---
sys.setrecursionlimit(5000)
# -----------------------

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 1. Define your data files (matches your --add-data flags)
datas = [
    ('templates', 'templates'), 
    ('static', 'static'), 
    ('grafana_dashboards', 'grafana_dashboards'), 
    ('input.yaml', '.'), 
    ('alarms.yaml', '.'), 
    ('alarmList.json', '.'), 
    ('nodeid.yaml', '.'), 
    ('grafana_custom.ini', '.')
]

# 2. Define hidden imports (matches your --hidden-import flags)
hiddenimports = [
    'engineio.async_drivers.eventlet', 
    'pyodbc', 
    'db', 
    'requests',
    'sqlalchemy.sql.default_comparator' # Sometimes needed for sqlalchemy
]

binaries = []

# 3. Collect all data for complex packages (matches --collect-all)
tmp_ret = collect_all('dns')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('eventlet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 4. Exclude heavy libraries you aren't using to speed up build
# Based on your logs, PyInstaller is trying to bundle torch and tensorflow checks
excludes = ['tkinter', 'matplotlib', 'scipy', 'pandas', 'numpy', 'tensorflow', 'torch']

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes, 
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TSEScada',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TSEScada',
)