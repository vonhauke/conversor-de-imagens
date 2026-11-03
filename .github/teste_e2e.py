"""Teste de ponta a ponta do Conversor de Imagens (automação do GitHub e uso local).

Abre a janela real (pywebview) e usa o app como uma pessoa: adiciona imagens e uma
pasta com subpastas, converte, confere "só o que falta", troca o formato (o que libera
tudo de novo), mexe na qualidade, testa .raw sem dimensões no nome, um arquivo com
erro (e o "ver detalhes"), e cancela um lote grande, conferindo a tela e o disco.

uso: python .github/teste_e2e.py
Sai com código 1 se algo falhar.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from PIL import Image  # noqa: E402

TMP = tempfile.mkdtemp(prefix="imagens-teste-")
import main as app_mod  # noqa: E402

app_mod.CONFIG_PATH = os.path.join(TMP, "config", "config.json")   # não mexe nas escolhas reais
import argila  # noqa: E402
import webview  # noqa: E402

SAIDA = os.path.join(TMP, "saida")
CAPTURAS = os.environ.get("ARGILA_CAPTURAS")
falhas = []


def preparar():
    fotos = []
    for i, cor in enumerate([(220, 120, 90), (90, 160, 120), (60, 90, 200)]):
        p = os.path.join(TMP, f"foto{i}.png")
        Image.new("RGB", (320, 200), cor).save(p)
        fotos.append(p)
    album = os.path.join(TMP, "album")
    os.makedirs(os.path.join(album, "viagem"))
    Image.new("RGB", (120, 80), (200, 200, 60)).save(os.path.join(album, "capa.jpg"))
    Image.new("RGB", (120, 80), (40, 40, 40)).save(os.path.join(album, "viagem", "praia.webp"))
    raw = os.path.join(TMP, "glitch.raw")
    with open(raw, "wb") as f:
        f.write(bytes(range(256)) * (16 * 12 * 3 // 256 + 1))
    quebrado = os.path.join(TMP, "quebrado.png")
    with open(quebrado, "wb") as f:
        f.write(b"isto nao e uma imagem")
    muitas = os.path.join(TMP, "muitas")
    os.makedirs(muitas)
    for i in range(120):
        Image.new("RGB", (900, 600), (i % 255, 100, 150)).save(os.path.join(muitas, f"m{i:03d}.png"))
    return fotos, album, raw, quebrado, muitas


def verificar(cond, mensagem):
    print(("ok   " if cond else "FALHA ") + mensagem, flush=True)
    if not cond:
        falhas.append(mensagem)


def roteiro(janela, app, arquivos):
    fotos, album, raw, quebrado, muitas = arquivos
    js = janela.evaluate_js

    def esperar(condicao, limite=120, nome=""):
        fim = time.time() + limite
        while time.time() < fim:
            try:
                if js(condicao):
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        verificar(False, f"tempo esgotado esperando: {nome or condicao}")
        return False

    def capturar(nome):
        if CAPTURAS and sys.platform.startswith("linux"):
            time.sleep(0.6)
            subprocess.run(["import", "-window", "root", os.path.join(CAPTURAS, nome + ".png")])

    def clicar(seletor):
        js(f"document.querySelector({json.dumps(seletor)}).click()")

    def status():
        return json.loads(js("JSON.stringify(Conversor._s.itens.map(i => i.status))"))

    def saidas():
        return sorted(os.listdir(SAIDA)) if os.path.isdir(SAIDA) else []

    def converter(nome):
        """Clica em Converter e espera o lote terminar (o evento "fim" chegar)."""
        antes = js("Conversor._s.lotes")
        clicar("#btn-converter")
        return esperar(f"Conversor._s.lotes > {antes} && !Conversor._s.convertendo", 180, nome)

    try:
        esperar("document.documentElement.classList.contains('pronto')", 30, "interface pronta")
        capturar("01-vazio")
        app.pasta_escolhida = SAIDA
        os.makedirs(SAIDA, exist_ok=True)

        # 1) fotos + pasta com subpasta
        app.adicionar(fotos + [album])
        verificar(esperar("Conversor._s.itens.length === 5", 10, "5 itens"), "pasta entra com as imagens das subpastas")
        verificar(converter("lote 1"), "lote termina")
        verificar(len(saidas()) == 5 and all(s.endswith(".jpg") for s in saidas()), f"5 JPG gerados ({saidas()})")
        verificar(js("document.getElementById('btn-converter').disabled") is True,
                  "com tudo convertido, o botão fica desativado")
        capturar("02-concluido")

        # 2) só o que falta
        app.adicionar([quebrado])
        esperar("Conversor._s.itens.length === 6", 10, "6 itens")
        verificar(js("document.getElementById('btn-converter-texto').textContent") == "Converter 1 restante",
                  "botão mostra 'Converter 1 restante'")
        converter("lote 2")
        verificar(status()[-1] == "erro" and status().count("concluido") == 5, f"só o novo é tentado ({status()})")
        clicar('.item[data-i="5"] .ver-log')
        verificar(esperar("!document.getElementById('modal-log').classList.contains('oculto') && "
                          "document.getElementById('log-texto').textContent.includes('Traceback')", 10, "log"),
                  "'ver detalhes' mostra o erro")
        capturar("03-erro")
        clicar("#log-ok")
        clicar('.item[data-i="5"] .remover')
        esperar("Conversor._s.itens.length === 5", 10, "remover")

        # 3) trocar formato libera tudo; qualidade aparece só para formatos com perda
        clicar('#formatos button[data-f="png"]')
        verificar(esperar("Conversor._s.itens.every(i => i.status === 'pendente')", 10, "pendentes"),
                  "trocar o formato libera tudo para converter de novo")
        verificar(js("document.getElementById('painel-qualidade').classList.contains('oculto')") is True,
                  "PNG não mostra qualidade")
        clicar('#formatos button[data-f="webp"]')
        verificar(js("!document.getElementById('painel-qualidade').classList.contains('oculto')") is True,
                  "WEBP mostra o controle de qualidade")
        js("const q = document.getElementById('qualidade'); q.value = 55; q.dispatchEvent(new Event('change'))")
        esperar("true", 1)
        verificar(app.opcoes["qualidade"] == 55, "qualidade escolhida chega ao conversor")
        converter("lote webp")
        verificar(len([s for s in saidas() if s.endswith(".webp")]) == 5, "5 WEBP gerados")
        capturar("04-webp")

        # 4) .raw sem dimensões no nome
        clicar('#formatos button[data-f="png"]')
        app.adicionar([raw])
        verificar(esperar("!document.getElementById('painel-raw').classList.contains('oculto')", 10, "painel raw"),
                  "painel do .raw aparece")
        js("document.getElementById('raw-largura').value = '16'; document.getElementById('raw-altura').value = '12'")
        converter("lote raw")
        verificar("glitch_convertido.png" in saidas(), "o .raw vira PNG com as dimensões digitadas")

        # 5) cancelar um lote grande
        for i in range(len(app.files) - 1, -1, -1):
            app.remover(app.files[i]["path"])
        app.adicionar([muitas])
        esperar("Conversor._s.itens.length === 120", 20, "120 itens")
        clicar('#formatos button[data-f="tiff"]')
        antes = js("Conversor._s.lotes")
        clicar("#btn-converter")
        esperar("Conversor._s.itens.some(i => i.status === 'concluido')", 60, "começou")
        capturar("05-convertendo")
        clicar("#btn-cancelar")
        verificar(esperar(f"Conversor._s.lotes > {antes} && !Conversor._s.convertendo", 120, "cancelou"),
                  "cancelar para o lote")
        st = status()
        verificar("convertendo" not in st and "pendente" not in st, "nada fica pela metade depois de cancelar")

        erros_js = js("JSON.stringify(window.__erros)")
        verificar(erros_js in ("[]", None), f"sem erros de JavaScript ({erros_js})")
    except Exception:
        traceback.print_exc()
        falhas.append("exceção no roteiro")
    finally:
        janela.destroy()


def main():
    arquivos = preparar()
    emitir = argila.Emissor("Conversor")
    app = app_mod.App(emitir)
    janela = argila.criar_janela("Conversor de Imagens (teste)", app, 800, 820, 660, 580)
    app._janela = emitir.janela = janela
    webview.start(roteiro, (janela, app, arquivos), http_server=True, private_mode=True)
    shutil.rmtree(TMP, ignore_errors=True)
    if falhas:
        print(f"\n{len(falhas)} falha(s):\n- " + "\n- ".join(falhas))
        sys.exit(1)
    print("\nTudo certo: o conversor de imagens funcionou de ponta a ponta.")


if __name__ == "__main__":
    main()
