# Build tool only: PyInstaller is not imported by the application.
from pathlib import Path
root = Path(SPECPATH)
excluded = ['PIL', 'yaml', 'watchdog', 'send2trash', 'pystray', 'pypdf', 'docx']
a = Analysis([str(root / 'FileNazm.py')], pathex=[str(root)], binaries=[],
             datas=[(str(root / 'assets'), 'assets'),
                    (str(root / 'FileNazm.ico'), '.'),
                    (str(root / 'LICENSE.txt'), '.'),
                    (str(root / 'THIRD-PARTY-LICENSES.txt'), '.')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=excluded, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='FileNazm',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon=str(root / 'FileNazm.ico'))
