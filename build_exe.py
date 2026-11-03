"""Gera "Conversor de Imagens.exe" (arquivo único) em dist/.

Uso:
    pip install pyinstaller pywebview pillow pillow-heif
    python build_exe.py
"""

import os
import PyInstaller.__main__

AQUI = os.path.dirname(os.path.abspath(__file__))

PyInstaller.__main__.run([
    os.path.join(AQUI, "main.py"),
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "Conversor de Imagens",
    "--icon", os.path.join(AQUI, "icone_conversor.ico"),
    "--add-data", f"{os.path.join(AQUI, 'icone_conversor.png')};.",
    "--add-data", f"{os.path.join(AQUI, 'ui')};ui",   # a interface (HTML/CSS/JS, fontes e icones)
    "--collect-all", "webview",                     # pywebview (janela com o Edge WebView2)
    "--collect-all", "pillow_heif",
])
