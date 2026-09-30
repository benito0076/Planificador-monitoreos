# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('C:/Users/Usuario/.local/bin/planificador_monitoreo/monitoreo/interfaz.html', 'monitoreo')]
datas += collect_data_files('folium')
datas += collect_data_files('branca')
datas += collect_data_files('xyzservices')


a = Analysis(
    ['../main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['folium', 'openpyxl'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'matplotlib', 'scipy', 'PIL', 'IPython', 'tkinter', 'geopandas', 'shapely'],
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
    name='PlanificadorMonitoreo',
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
