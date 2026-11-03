"""Conversor de Imagens.

Converte, em lote, entre JPG, PNG, WEBP, GIF, BMP, TIFF, ICO, HEIC, AVIF, TGA, PPM e .raw
(pixels puros sem cabeçalho, usado em fluxos de glitch art). Usa a Pillow (e pillow-heif).

Interface: HTML/CSS em ui/ (design system "Argila", em ui/ds/), mostrada numa janela
nativa pelo pywebview (Edge WebView2 no Windows, WebKit no Mac), com o mesmo visual do
Conversor de Vídeos. Arquivos ou pastas inteiras podem ser arrastados para qualquer ponto
da janela ou escolhidos pelo botão "Adicionar arquivos" / clique na área de soltar.

A classe App guarda a fila e as escolhas e roda o lote; não depende da janela (os testes
em tests/ a usam diretamente). A interface chama os métodos públicos dela.
"""

import json
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import image_utils as iu

TITULO = "Conversor de Imagens"

STATUS_PENDENTE = "pendente"
STATUS_CONVERTENDO = "convertendo"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"
STATUS_CANCELADO = "cancelado"

CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "Conversor de Imagens", "config.json"
)


def carregar_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def salvar_config(dados):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # não lembrar as escolhas não impede o uso


def _format_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            texto = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            return texto.replace(".", ",")
        size /= 1024
    return ""


def _abreviar(caminho, limite=60):
    if len(caminho) <= limite:
        return caminho
    return caminho[:18] + "…" + caminho[-(limite - 19):]


def _plural(n, um, varios):
    return f"{n} {um if n == 1 else varios}"


class App:
    """Fila, escolhas e lote. `emitir(evento)` avisa a interface (pode ser chamada de
    qualquer thread). Métodos sem _ são chamados pela interface (window.pywebview.api)."""

    def __init__(self, emitir=None):
        self._emitir = emitir or (lambda evento: None)
        self._janela = None
        self._trava = threading.RLock()

        # Cada item: {"path", "status", "log"}
        self.files = []

        cfg = carregar_config()
        formato = cfg.get("formato") if cfg.get("formato") in iu.OUTPUT_FORMATS else "jpg"
        qualidade = cfg.get("qualidade") if isinstance(cfg.get("qualidade"), int) else 90
        pix_in = cfg.get("raw_in_pix_fmt") if cfg.get("raw_in_pix_fmt") in iu.RAW_PIXEL_FORMATS else "rgb24"
        pix_out = cfg.get("raw_out_pix_fmt") if cfg.get("raw_out_pix_fmt") in iu.RAW_PIXEL_FORMATS else "rgb24"
        pasta = cfg.get("pasta_saida") if isinstance(cfg.get("pasta_saida"), str) else ""

        # Pasta escolhida em "Trocar" (lembrada entre usos) ou, se não houver, a da primeira imagem.
        self.pasta_escolhida = pasta if os.path.isdir(pasta) else ""
        self.pasta_padrao = ""
        self.opcoes = {
            "formato": formato,
            "qualidade": min(max(qualidade, 1), 100),
            # .raw de entrada cujo nome não traz as dimensões (ex.: _1920x1080_rgb24)
            "raw_in_largura": str(cfg.get("raw_in_largura", "1920")),
            "raw_in_altura": str(cfg.get("raw_in_altura", "1080")),
            "raw_in_pix_fmt": pix_in,
            "raw_out_pix_fmt": pix_out,
        }

        self._cancel_event = threading.Event()
        self._converting = False
        self._config_mudou_durante_lote = False

    # ------------------------------------------------------------ estado
    def _config_atual(self):
        return dict(self.opcoes, pasta_saida=self.pasta_escolhida)

    def _pasta_saida(self):
        return self.pasta_escolhida or self.pasta_padrao

    def _raw_sem_nome(self):
        """Há .raw na fila cujo nome não diz largura, altura e formato de pixel?"""
        return any(iu.is_raw(i["path"]) and iu.raw_params_from_name(i["path"]) is None for i in self.files)

    def _resumo(self):
        """Texto da barra de baixo e o botão Converter (rótulo e se está habilitado)."""
        total = len(self.files)
        if total == 0:
            return {"texto": "Nenhum arquivo na fila", "botao": "Converter tudo", "habilitado": False}
        done = sum(1 for f in self.files if f["status"] == STATUS_CONCLUIDO)
        errors = sum(1 for f in self.files if f["status"] == STATUS_ERRO)
        restantes = total - done
        if restantes == 0:
            return {"texto": f"{_plural(done, 'concluído', 'concluídos')} · troque o formato ou adicione mais arquivos",
                    "botao": "Converter tudo", "habilitado": False}
        if done or errors:
            partes = []
            if done:
                partes.append(_plural(done, "concluído", "concluídos"))
            if errors:
                partes.append(f"{errors} com erro")
            texto = " · ".join(partes)
        else:
            texto = f"{_plural(total, 'arquivo', 'arquivos')} na fila"
        botao = f"Converter {_plural(restantes, 'restante', 'restantes')}" if done else "Converter tudo"
        return {"texto": texto, "botao": botao, "habilitado": True}

    def _destino(self):
        if self.pasta_escolhida:
            return {"texto": _abreviar(self.pasta_escolhida, 56), "caminho": self.pasta_escolhida, "escolhida": True}
        return {"texto": _abreviar(self.pasta_padrao) or "Pasta da primeira imagem adicionada",
                "caminho": self.pasta_padrao, "escolhida": False}

    def _item_publico(self, item):
        path = item["path"]
        return {"path": path, "nome": os.path.basename(path),
                "ext": os.path.splitext(path)[1].lstrip(".").upper()[:4] or "?",
                "tamanho": _format_size(path), "status": item["status"]}

    def _enviar_fila(self):
        with self._trava:
            itens = [self._item_publico(i) for i in self.files]
        self._emitir({"tipo": "fila", "itens": itens, "resumo": self._resumo(), "destino": self._destino(),
                      "raw_sem_nome": self._raw_sem_nome(), "convertendo": self._converting})

    def _set_item_status(self, path, status):
        with self._trava:
            for item in self.files:
                if item["path"] == path:
                    item["status"] = status
        self._emitir({"tipo": "item", "path": path, "status": status})

    def _config_mudou(self):
        """Com outro formato, qualidade ou pasta, o que já foi convertido precisa ser feito de novo."""
        if self._converting:
            self._config_mudou_durante_lote = True
            return
        with self._trava:
            for item in self.files:
                if item["status"] == STATUS_CONCLUIDO:
                    item["status"] = STATUS_PENDENTE
        self._enviar_fila()

    # ------------------------------------------------------- chamados pela interface
    def estado_inicial(self):
        return {"formatos": iu.OUTPUT_FORMATS, "com_perda": sorted(iu.LOSSY_FORMATS),
                "pix_fmts": iu.RAW_PIXEL_FORMATS, "opcoes": self.opcoes, "destino": self._destino()}

    def definir_opcoes(self, novas):
        """Formato, qualidade e parâmetros de .raw escolhidos na tela (valores inválidos são ignorados)."""
        mudou = False
        for chave, valor in (novas or {}).items():
            if chave == "formato" and valor not in iu.OUTPUT_FORMATS:
                continue
            if chave in ("raw_in_pix_fmt", "raw_out_pix_fmt") and valor not in iu.RAW_PIXEL_FORMATS:
                continue
            if chave == "qualidade":
                try:
                    valor = min(max(int(valor), 1), 100)
                except (TypeError, ValueError):
                    continue
            if chave in self.opcoes and self.opcoes[chave] != valor:
                self.opcoes[chave] = valor
                mudou = True
        if mudou:
            self._config_mudou()

    def escolher_arquivos(self):
        if self._converting or self._janela is None:
            return
        import argila

        padrao = ";".join(f"*{ext}" for ext in sorted(iu.INPUT_EXTENSIONS))
        escolhidos = self._janela.create_file_dialog(
            argila.TIPO_ABRIR, allow_multiple=True,
            file_types=(f"Imagens ({padrao})", "Todos os arquivos (*.*)"))
        if escolhidos:
            self.adicionar(list(escolhidos))

    @staticmethod
    def _expandir(paths):
        """Arquivos soltos entram como estão; pastas entram com as imagens de dentro (e subpastas)."""
        for p in paths:
            if not p:
                continue
            if os.path.isdir(p):
                for raiz, pastas, nomes in os.walk(p):
                    pastas.sort()
                    for nome in sorted(nomes):
                        if iu.is_supported(nome):
                            yield os.path.join(raiz, nome)
            elif os.path.isfile(p):
                yield p

    def adicionar(self, paths):
        """Também usado pelo arrastar e soltar."""
        if self._converting:
            return
        paths = [os.path.normpath(p) for p in self._expandir(paths)]
        if not paths:
            return
        with self._trava:
            existentes = {item["path"] for item in self.files}
            for path in paths:
                if path not in existentes:
                    existentes.add(path)
                    self.files.append({"path": path, "status": STATUS_PENDENTE, "log": ""})
            if not self.pasta_padrao:
                self.pasta_padrao = os.path.dirname(paths[0])
        self._enviar_fila()

    _add_paths = adicionar

    def remover(self, path):
        if self._converting:
            return
        with self._trava:
            self.files = [item for item in self.files if item["path"] != path]
        self._enviar_fila()

    def ver_log(self, path):
        item = next((f for f in self.files if f["path"] == path), None)
        if not item or not item["log"]:
            return ""
        return "\n".join(item["log"].strip().splitlines()[-25:])

    def trocar_pasta(self):
        if self._janela is None:
            return
        import argila

        atual = self._pasta_saida()
        escolha = self._janela.create_file_dialog(argila.TIPO_PASTA, directory=atual if os.path.isdir(atual) else "")
        if escolha:
            self.pasta_escolhida = os.path.normpath(escolha[0] if isinstance(escolha, (list, tuple)) else escolha)
            self._config_mudou()

    def usar_pasta_padrao(self):
        self.pasta_escolhida = ""
        self._config_mudou()

    def abrir_pasta(self):
        pasta = self._pasta_saida()
        if not pasta:
            return
        import argila

        try:
            os.makedirs(pasta, exist_ok=True)
            argila.abrir_no_sistema(pasta)
        except Exception as e:
            self._emitir({"tipo": "aviso", "titulo": "Não foi possível abrir a pasta", "texto": str(e)[:200]})

    def cancelar(self):
        self._cancel_event.set()

    _cancel_batch = cancelar

    def fechar(self):
        salvar_config(self._config_atual())
        self._cancel_event.set()  # imagens que ainda não começaram não são convertidas

    _fechar = fechar

    def converter(self):
        """Converte o que ainda não foi convertido. Devolve {"titulo", "texto"} se algo impedir."""
        if self._converting:
            return None
        with self._trava:
            items = [f for f in self.files if f["status"] != STATUS_CONCLUIDO]
        if not items:
            return None
        out_dir = self._pasta_saida()
        if not out_dir:
            return {"titulo": "Atenção", "texto": "Escolha uma pasta de saída."}

        raw_in = None
        if self._raw_sem_nome():
            try:
                raw_in = {
                    "width": int(str(self.opcoes["raw_in_largura"]).strip()),
                    "height": int(str(self.opcoes["raw_in_altura"]).strip()),
                    "pix_fmt": self.opcoes["raw_in_pix_fmt"],
                }
                if raw_in["width"] <= 0 or raw_in["height"] <= 0:
                    raise ValueError
            except ValueError:
                return {"titulo": "Atenção", "texto": "Largura e altura do .raw devem ser números maiores que zero."}

        out_format = self.opcoes["formato"]
        raw_out = {"pix_fmt": self.opcoes["raw_out_pix_fmt"]} if out_format == "raw" else None

        with self._trava:
            for item in items:
                item["status"], item["log"] = STATUS_PENDENTE, ""

        salvar_config(self._config_atual())
        self._cancel_event.clear()
        self._converting = True
        self._config_mudou_durante_lote = False
        self._enviar_fila()
        self._emitir({"tipo": "inicio", "total": len(items)})
        threading.Thread(
            target=self._run_batch_thread,
            args=(items, out_dir, out_format, self.opcoes["qualidade"], raw_in, raw_out),
            daemon=True,
        ).start()
        return None

    _start_batch = converter

    # ------------------------------------------------------------ o lote
    def _run_batch_thread(self, items, out_dir, out_format, quality, raw_in, raw_out):
        total = len(items)
        used_names = set()
        lock = threading.Lock()
        contagem = {"ok": 0, "erro": 0, "prontas": 0}
        os.makedirs(out_dir, exist_ok=True)

        def caminho_saida(base_name, ext):
            with lock:
                return self._unique_output_path(base_name, ext, out_dir, used_names)

        def converter(item):
            path = item["path"]
            if self._cancel_event.is_set():
                self._set_item_status(path, STATUS_CANCELADO)
                return
            self._set_item_status(path, STATUS_CONVERTENDO)
            base_name = os.path.splitext(os.path.basename(path))[0]
            try:
                iu.convert(
                    path, out_format, lambda ext: caminho_saida(base_name, ext),
                    quality=quality, raw_in=raw_in, raw_out=raw_out,
                )
                status = STATUS_CONCLUIDO
            except Exception:
                item["log"] = traceback.format_exc()
                status = STATUS_ERRO
            with lock:
                contagem["ok" if status == STATUS_CONCLUIDO else "erro"] += 1
                contagem["prontas"] += 1
                prontas = contagem["prontas"]
            self._set_item_status(path, status)
            self._emitir({"tipo": "progresso", "prontas": prontas, "total": total,
                          "cancelando": self._cancel_event.is_set()})

        with ThreadPoolExecutor(max_workers=iu.parallel_workers(out_format)) as pool:
            list(pool.map(converter, items))

        self._on_batch_done(contagem["ok"], contagem["erro"], total)

    @staticmethod
    def _unique_output_path(base_name, ext, out_dir, used_names):
        """nome_convertido.ext, sem repetir nomes do lote nem sobrescrever arquivos que já existem."""
        candidate = f"{base_name}_convertido{ext}"
        counter = 1
        while candidate in used_names or os.path.exists(os.path.join(out_dir, candidate)):
            candidate = f"{base_name}_convertido_{counter}{ext}"
            counter += 1
        used_names.add(candidate)
        return os.path.join(out_dir, candidate)

    def _on_batch_done(self, done_count, error_count, total):
        if self._cancel_event.is_set():
            resumo = f"Cancelado. {done_count}/{total} concluídos antes de parar."
        elif error_count:
            resumo = f"Concluído com erros: {done_count} ok, {error_count} com erro de {total}."
        else:
            resumo = f"Concluído: {done_count}/{total} arquivos convertidos."
        self._converting = False
        if self._config_mudou_durante_lote:
            self._config_mudou()
        self._enviar_fila()
        self._emitir({"tipo": "fim", "resumo": resumo, "feitos": done_count, "erros": error_count, "total": total})


def ligar_arrastar_e_soltar(janela, app):
    """Arquivos e pastas arrastados para a janela (o pywebview informa o caminho completo)."""
    try:
        from webview.dom import DOMEventHandler

        def tratar(evento):
            arquivos = (evento.get("dataTransfer") or {}).get("files") or []
            app.adicionar([f.get("pywebviewFullPath") for f in arquivos])

        janela.dom.document.events.drop += DOMEventHandler(tratar, True, True)
    except Exception:
        pass   # sem arrastar e soltar, o botão de escolher arquivos continua valendo


def main():
    import argila

    emitir = argila.Emissor("Conversor")
    app = App(emitir)
    janela = argila.criar_janela(TITULO, app, 800, 820, 660, 580)
    app._janela = emitir.janela = janela
    janela.events.loaded += lambda: ligar_arrastar_e_soltar(janela, app)
    janela.events.closing += app.fechar
    argila.iniciar(icone="icone_conversor.png", titulo=TITULO)


if __name__ == "__main__":
    main()
