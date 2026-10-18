"""Gera "Conversor de Imagens.exe" (arquivo único) em dist/.

Uso:
    pip install pyinstaller customtkinter pillow pillow-heif tkinterdnd2
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
    "--collect-all", "customtkinter",
    "--collect-all", "tkinterdnd2",
    "--collect-all", "pillow_heif",
])
