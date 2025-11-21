# project.spec
# PyInstaller spec for CAD_Reposition_Helper (PyWebView + PyQt5 + PyQtWebEngine)
block_cipher = None

import os
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules

# project root
project_root = os.getcwd()
WEB_ROOT = os.path.join(project_root, "output")

# Ensure pathex contains project root
pathex = [project_root]

# Collect PyQt5 / PyQtWebEngine libraries & data so QtWebEngine resources are present
pyqt5_binaries = collect_dynamic_libs('PyQt5') or []
pyqt5_datas = collect_data_files('PyQt5') or []
# PyQtWebEngine may provide additional runtime libraries/resources
pyqtwebengine_binaries = collect_dynamic_libs('PyQtWebEngine') or []
pyqtwebengine_datas = collect_data_files('PyQtWebEngine') or []

# Collect submodules for qtpy and webview to avoid missing imports
qtpy_hidden = collect_submodules('qtpy') or []
pywebview_hidden = collect_submodules('webview') or []

# Your project files/folders to include
project_datas = [
    ('web', 'web'),        # include the whole web package (HTML/JS templates)
    (WEB_ROOT, 'output')   # include output folder (viewer.html etc.)
]

# Combine binaries and datas
binaries = pyqt5_binaries + pyqtwebengine_binaries
datas = pyqt5_datas + pyqtwebengine_datas + project_datas

# Hidden imports: keep the ones you had plus collects
hiddenimports = [
    # OCP (OCCT python bindings)
    'OCP.AIS',
    'OCP.BRep',
    'OCP.BRepAdaptor',
    'OCP.BRepBuilderAPI',
    'OCP.BRepMesh',
    'OCP.BRepTools',
    'OCP.BRepGProp',
    'OCP.GC',
    'OCP.gp',
    'OCP.Geom',
    'OCP.GeomAbs',
    'OCP.GeomAPI',
    'OCP.GeomConvert',
    'OCP.Geom2d',
    'OCP.TopoDS',
    'OCP.TopExp',
    'OCP.TopAbs',
    'OCP.Interface',
    'OCP.STEPControl',
    'OCP.IFSelect',
    'OCP.ShapeFix',
    'OCP.ShapeAnalysis',
    'OCP.GProp',
    # web-related
    'webview',
    'requests',
    'qtpy'
] + qtpy_hidden + pywebview_hidden

# Analysis
a = Analysis(
    ['main.py'],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CAD_Reposition_Helper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None
)
