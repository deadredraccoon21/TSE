# -*- mode: python ; coding: utf-8 -*-
import sys
sys.setrecursionlimit(5000)

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 1. Define your data files
datas = [
    ('templates', 'templates'), 
    ('static', 'static'), 
    ('grafana_dashboards', 'grafana_dashboards'), 
    ('input.yaml', '.'), 
    ('predefined_departments.yml', '.'),  # Ensure this matches the .yml extension in app.py
    ('alarms.yaml', '.'), 
    ('alarmList.json', '.'), 
    ('nodeid.yaml', '.'), 
    ('grafana_custom.ini', '.'),
    ('shift_data.json', '.') # FIX: Added the destination path '.'
]

# 2. Define hidden imports
hiddenimports = [
    'engineio.async_drivers.gevent', 
    'gevent',          # Force PyInstaller to grab the base library
    'gevent.monkey',   # Force it to grab the monkey patch module
    'pyodbc', 
    'db', 
    'requests',
    'sqlalchemy.sql.default_comparator',
    'shift_report' 
]

binaries = []

# 3. Collect all data for complex packages
tmp_ret = collect_all('dns')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('gevent') # FIX: Collect gevent instead of eventlet
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('playwright') 
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 4. Exclude heavy libraries
# FIX: Removed numpy and pandas so Plotly doesn't crash
excludes = ['tkinter', 'matplotlib', 'scipy', 'tensorflow', 'torch']

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
    console=True, # Keep True for debugging. Change to False later to hide the black terminal window.
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