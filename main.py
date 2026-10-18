"""Conversor de Imagens - GUI em CustomTkinter.

Converte, em lote, entre JPG, PNG, WEBP, GIF, BMP, TIFF, ICO, HEIC, AVIF, TGA, PPM e .raw
(pixels puros sem cabeçalho, usado em fluxos de glitch art). Usa a Pillow (e pillow-heif).

Mesmo visual do Conversor de Vídeos (grafite, marfim e âmbar). Arquivos ou pastas inteiras
podem ser arrastados do Explorer para qualquer ponto da janela (tkinterdnd2) ou escolhidos
pelo botão "+ Adicionar arquivos" / clique na área de soltar.
"""

import json
import os
import queue
import threading
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

import image_utils as iu

# ---------------------------------------------------------------- paleta
COL_BG = "#14171c"
COL_CARD = "#1c2027"
COL_CARD_ALT = "#232831"
COL_DROP_BG = "#171a20"
COL_DROP_BORDER = "#3a404b"
COL_BADGE_BG = "#2a2f38"
COL_TEXT = "#ece6d8"
COL_SUB = "#8b919b"
COL_MUTED = "#6c727c"
COL_DEST = "#b8b2a5"
COL_AMBER = "#d4a24c"
COL_AMBER_TEXT = "#1a1408"
COL_OK = "#7fb38a"
COL_ERR = "#e07a6a"
COL_X = "#4c525c"
COL_TRACK = "#2a2f38"

FONT = "Segoe UI"

STATUS_PENDENTE = "Pendente"
STATUS_CONVERTENDO = "Convertendo..."
STATUS_CONCLUIDO = "Concluído"
STATUS_ERRO = "Erro"
STATUS_CANCELADO = "Cancelado"

# Linhas da fila criadas por vez: soltar uma pasta com centenas de fotos não congela a janela.
ROWS_PER_TICK = 25

_HERE = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(_HERE, "icone_conversor.png")
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
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class FileRow(ctk.CTkFrame):
    """Uma linha da fila: badge da extensão, nome/tamanho, status e remover."""

    def __init__(self, master, path, on_remove, on_view_log):
        super().__init__(master, fg_color="transparent", corner_radius=10)
        self.path = path
        self._on_remove = on_remove
        self._on_view_log = on_view_log

        self.grid_columnconfigure(1, weight=1)

        ext = os.path.splitext(path)[1].lstrip(".").upper()[:4] or "?"
        ctk.CTkLabel(
            self, text=ext, width=40, height=40, corner_radius=8,
            fg_color=COL_BADGE_BG, text_color=COL_DEST,
            font=(FONT, 10, "bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=10)

        ctk.CTkLabel(
            self, text=os.path.basename(path), text_color=COL_TEXT,
            font=(FONT, 13), anchor="w",
        ).grid(row=0, column=1, sticky="ew", pady=(10, 0))

        ctk.CTkLabel(
            self, text=_format_size(path), text_color=COL_MUTED,
            font=(FONT, 11), anchor="w",
        ).grid(row=1, column=1, sticky="ew", pady=(0, 10))

        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=0, column=2, rowspan=2, padx=(8, 2))
        self.status_label = ctk.CTkLabel(
            self.status_frame, text="Na fila", text_color=COL_MUTED,
            font=(FONT, 12, "bold"),
        )
        self.status_label.pack(side="left")
        self.detail_btn = ctk.CTkButton(
            self.status_frame, text="ver detalhes", width=1, height=18,
            fg_color="transparent", hover_color=COL_CARD_ALT,
            text_color=COL_ERR, font=(FONT, 12, "bold", "underline"),
            command=lambda: self._on_view_log(self.path),
        )

        ctk.CTkButton(
            self, text="✕", width=20, height=20, fg_color="transparent",
            hover_color=COL_CARD_ALT, text_color=COL_X, font=(FONT, 12),
            command=lambda: self._on_remove(self.path),
        ).grid(row=0, column=3, rowspan=2, padx=(4, 10))

    def set_status(self, status):
        self.detail_btn.pack_forget()
        self.configure(fg_color="transparent")

        if status == STATUS_PENDENTE:
            self.status_label.configure(text="Na fila", text_color=COL_MUTED)
        elif status == STATUS_CONVERTENDO:
            self.status_label.configure(text="Convertendo…", text_color=COL_AMBER)
            self.configure(fg_color=COL_CARD_ALT)
        elif status == STATUS_CONCLUIDO:
            self.status_label.configure(text="✓ Concluído", text_color=COL_OK)
        elif status == STATUS_ERRO:
            self.status_label.configure(text="Erro ·", text_color=COL_ERR)
            self.detail_btn.pack(side="left", padx=(4, 0))
        elif status == STATUS_CANCELADO:
            self.status_label.configure(text="Cancelado", text_color=COL_MUTED)


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self._dnd_ok = self._ativar_arrastar_e_soltar()
        ctk.set_appearance_mode("dark")

        self.title("Conversor de Imagens")
        self._ajustar_janela(760, 800, 720, 580)
        self._altura_max_real = self._cliente_max_real()[1]
        self.configure(fg_color=COL_BG)
        self._set_window_icon()

        # Cada item: {"path", "status", "log", "row"} (row = None até a linha ser desenhada)
        self.files = []
        self._rows_pendentes = []

        cfg = carregar_config()
        formato = cfg.get("formato") if cfg.get("formato") in iu.OUTPUT_FORMATS else "jpg"
        qualidade = cfg.get("qualidade") if isinstance(cfg.get("qualidade"), int) else 90
        pix_in = cfg.get("raw_in_pix_fmt") if cfg.get("raw_in_pix_fmt") in iu.RAW_PIXEL_FORMATS else "rgb24"
        pix_out = cfg.get("raw_out_pix_fmt") if cfg.get("raw_out_pix_fmt") in iu.RAW_PIXEL_FORMATS else "rgb24"
        pasta = cfg.get("pasta_saida") if isinstance(cfg.get("pasta_saida"), str) else ""

        # Pasta escolhida em "Trocar" (lembrada entre usos) ou, se não houver, a da primeira imagem.
        self.pasta_escolhida = pasta if os.path.isdir(pasta) else ""
        self.pasta_padrao = ""
        self.output_format = tk.StringVar(value=formato)
        self.quality = tk.IntVar(value=min(max(qualidade, 1), 100))

        # Parâmetros dos .raw de entrada cujo nome não traz as dimensões (ex.: _1920x1080_rgb24).
        self.raw_in_width = tk.StringVar(value=str(cfg.get("raw_in_largura", "1920")))
        self.raw_in_height = tk.StringVar(value=str(cfg.get("raw_in_altura", "1080")))
        self.raw_in_pix_fmt = tk.StringVar(value=pix_in)

        self.raw_out_pix_fmt = tk.StringVar(value=pix_out)

        self._cancel_event = threading.Event()
        # As threads de conversão não mexem na janela: deixam avisos aqui e a janela os lê.
        self._avisos = queue.Queue()
        self._converting = False
        self._config_mudou_durante_lote = False
        self._format_buttons = {}
        self._cor_borda_drop = COL_DROP_BORDER

        self._build_ui()
        self._encaixar_conteudo()

        for var in (self.output_format, self.quality, self.raw_in_width, self.raw_in_height,
                    self.raw_in_pix_fmt, self.raw_out_pix_fmt):
            var.trace_add("write", lambda *_: self._config_mudou())
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    def _config_atual(self):
        return {
            "formato": self.output_format.get(),
            "qualidade": self.quality.get(),
            "raw_in_largura": self.raw_in_width.get(),
            "raw_in_altura": self.raw_in_height.get(),
            "raw_in_pix_fmt": self.raw_in_pix_fmt.get(),
            "raw_out_pix_fmt": self.raw_out_pix_fmt.get(),
            "pasta_saida": self.pasta_escolhida,
        }

    def _fechar(self):
        salvar_config(self._config_atual())
        self._cancel_event.set()  # imagens que ainda não começaram não são convertidas
        self.destroy()

    def _config_mudou(self):
        """Com outro formato, qualidade ou pasta, o que já foi convertido precisa ser feito de novo."""
        if self._converting:
            self._config_mudou_durante_lote = True
            return
        mudou = False
        for item in self.files:
            if item["status"] == STATUS_CONCLUIDO:
                self._set_item_status(item["path"], STATUS_PENDENTE)
                mudou = True
        if mudou:
            self._refresh_idle_text()

    def _ativar_arrastar_e_soltar(self):
        """Faz a janela inteira aceitar arquivos arrastados do Explorer."""
        try:
            self.TkdndVersion = TkinterDnD._require(self)
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.dnd_bind("<<DropEnter>>", lambda e: self._destacar_area(True))
            self.dnd_bind("<<DropLeave>>", lambda e: self._destacar_area(False))
            return True
        except Exception:
            return False  # sem arrastar e soltar, o botão de escolher arquivos continua valendo

    def _destacar_area(self, ativo):
        self._cor_borda_drop = COL_AMBER if ativo else COL_DROP_BORDER
        self.drop_zone.event_generate("<Configure>")

    def _on_drop(self, event):
        self._destacar_area(False)
        self._add_paths(self.tk.splitlist(event.data))
        return event.action

    @staticmethod
    def _area_util(tela_w, tela_h):
        """Área de trabalho em pixels reais (sem a barra de tarefas); estimativa se não achar."""
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                return r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        return tela_w, int(tela_h * 0.93)

    def _cliente_max_real(self):
        """Maior área (em pixels reais) que o conteúdo da janela pode ocupar, já descontando
        a barra de título e as bordas do Windows e uma folga nas margens da tela."""
        escala = self._get_window_scaling()
        util_w, util_h = self._area_util(self.winfo_screenwidth(), self.winfo_screenheight())
        return util_w - int(24 * escala) - 16, util_h - int(52 * escala) - 16

    def _encaixar_conteudo(self):
        """Em telas baixas, reduz a escala dos elementos para que nada fique cortado,
        mesmo com os painéis de .raw e de opções abertos ao mesmo tempo."""
        self.raw_in_panel.grid(row=2, column=0, sticky="ew", padx=26, pady=(0, 14))
        self.raw_out_panel.grid(row=4, column=0, sticky="ew", padx=26, pady=(0, 14))
        self.update_idletasks()
        escala = self._get_window_scaling()
        lista_minima = 130 * escala
        necessario = self.winfo_reqheight() - self.body.winfo_reqheight() + lista_minima
        self._refresh_visibility()

        if necessario > self._altura_max_real:
            fator = max(0.7, self._altura_max_real / necessario)
            ctk.set_widget_scaling(fator)
            self.update_idletasks()

    def _ajustar_janela(self, largura, altura, min_largura, min_altura):
        """Abre a janela no tamanho ideal, mas nunca maior que a tela do computador em uso,
        centralizada. O CustomTkinter multiplica os valores pela escala do Windows (DPI),
        então converto o espaço disponível (pixels reais) para o tamanho "lógico"."""
        escala = self._get_window_scaling()
        max_w, max_h = (v / escala for v in self._cliente_max_real())

        largura, altura = min(largura, int(max_w)), min(altura, int(max_h))
        self.minsize(min(min_largura, largura), min(min_altura, altura))
        self.geometry(f"{largura}x{altura}")

        util_w, util_h = self._area_util(self.winfo_screenwidth(), self.winfo_screenheight())
        x = max((util_w - int(largura * escala) - int(24 * escala)) // 2, 0)
        y = max((util_h - int(altura * escala) - int(52 * escala)) // 2, 0)
        self.geometry(f"+{x}+{y}")

    def _set_window_icon(self):
        if not os.path.exists(ICON_PATH):
            return
        try:
            self._icon_ref = ImageTk.PhotoImage(Image.open(ICON_PATH))
            self.iconphoto(True, self._icon_ref)
        except (tk.TclError, OSError):
            pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self.raw_in_panel = self._build_raw_in_panel()
        self._build_format_row()
        self.raw_out_panel = self._build_raw_out_panel()
        self.quality_panel = self._build_quality_panel()
        self._build_dest_row()
        self._build_bottom_bar()

        self._refresh_visibility()
        self._refresh_bottom_bar()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 14))
        header.grid_columnconfigure(1, weight=1)

        if os.path.exists(ICON_PATH):
            icon_image = ctk.CTkImage(Image.open(ICON_PATH), size=(36, 36))
            ctk.CTkLabel(header, image=icon_image, text="").grid(
                row=0, column=0, rowspan=2, padx=(0, 12)
            )

        ctk.CTkLabel(
            header, text="Conversor de Imagens", text_color=COL_TEXT,
            font=(FONT, 20, "bold"), anchor="w",
        ).grid(row=0, column=1, sticky="w")
        self.subtitle_label = ctk.CTkLabel(
            header, text="Converta várias imagens de uma vez", text_color=COL_SUB,
            font=(FONT, 13), anchor="w",
        )
        self.subtitle_label.grid(row=1, column=1, sticky="w")

        ctk.CTkButton(
            header, text="+ Adicionar arquivos", fg_color=COL_CARD_ALT,
            hover_color=COL_BADGE_BG, text_color=COL_TEXT, corner_radius=10,
            height=36, font=(FONT, 13, "bold"), command=self._add_files,
        ).grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_body(self):
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 14))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.drop_zone = self._build_drop_zone(self.body)
        self.queue_frame = self._build_queue(self.body)

    def _build_drop_zone(self, master):
        canvas = tk.Canvas(
            master, bg=COL_DROP_BG, highlightthickness=0, bd=0, cursor="hand2",
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        def draw(_event=None):
            canvas.delete("border")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            if w > 4 and h > 4:
                canvas.create_rectangle(
                    2, 2, w - 2, h - 2, outline=self._cor_borda_drop, width=2,
                    dash=(6, 4), tags="border",
                )

        canvas.bind("<Configure>", draw)
        canvas.bind("<Button-1>", lambda e: self._add_files())

        content = ctk.CTkFrame(canvas, fg_color=COL_DROP_BG)
        content.place(relx=0.5, rely=0.5, anchor="center")
        content.bind("<Button-1>", lambda e: self._add_files())

        ctk.CTkLabel(
            content, text="⇧", text_color=COL_AMBER, font=(FONT, 30, "bold"),
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            content, text="Arraste imagens ou pastas para cá", text_color=COL_TEXT,
            font=(FONT, 17, "bold"),
        ).pack()

        line = ctk.CTkFrame(content, fg_color=COL_DROP_BG)
        line.pack(pady=(4, 0))
        ctk.CTkLabel(line, text="ou ", text_color=COL_SUB, font=(FONT, 13)).pack(side="left")
        link = ctk.CTkLabel(
            line, text="escolha arquivos", text_color=COL_AMBER,
            font=(FONT, 13, "bold"), cursor="hand2",
        )
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: self._add_files())
        ctk.CTkLabel(
            line, text=" · JPG, PNG, WEBP, HEIC, AVIF, GIF, RAW e mais", text_color=COL_SUB,
            font=(FONT, 13),
        ).pack(side="left")

        return canvas

    def _build_queue(self, master):
        frame = ctk.CTkScrollableFrame(
            master, fg_color=COL_CARD, corner_radius=14,
            scrollbar_button_color=COL_CARD_ALT,
            scrollbar_button_hover_color=COL_BADGE_BG,
        )
        frame.grid(row=0, column=0, sticky="nsew")
        return frame

    def _option_menu(self, master, variable, width):
        return ctk.CTkOptionMenu(
            master, variable=variable, values=iu.RAW_PIXEL_FORMATS,
            width=width, height=28, corner_radius=8, fg_color=COL_BADGE_BG,
            button_color=COL_BADGE_BG, button_hover_color=COL_CARD_ALT,
            text_color=COL_TEXT, font=(FONT, 12, "bold"),
            dropdown_fg_color=COL_CARD_ALT,
        )

    def _build_raw_in_panel(self):
        outer = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12)
        outer.grid_columnconfigure(1, weight=1)

        accent = ctk.CTkFrame(outer, fg_color=COL_DROP_BORDER, width=3, height=1, corner_radius=0)
        accent.grid(row=0, column=0, sticky="ns")

        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.grid(row=0, column=1, sticky="ew", padx=14, pady=10)

        title_row = ctk.CTkFrame(inner, fg_color="transparent")
        title_row.pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(
            title_row, text="Parâmetros dos arquivos .raw", text_color=COL_TEXT,
            font=(FONT, 13, "bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            title_row, text=" · para os .raw sem as dimensões no nome",
            text_color=COL_MUTED, font=(FONT, 12),
        ).pack(side="left")

        fields = ctk.CTkFrame(inner, fg_color="transparent")
        fields.pack(anchor="w")

        def field(label, var, width=60):
            col = ctk.CTkFrame(fields, fg_color="transparent")
            col.pack(side="left", padx=(0, 26))
            ctk.CTkLabel(
                col, text=label, text_color=COL_SUB, font=(FONT, 11, "bold"),
            ).pack(anchor="w")
            ctk.CTkEntry(
                col, textvariable=var, width=width, height=28, corner_radius=8,
                fg_color=COL_BADGE_BG, border_width=0, text_color=COL_TEXT,
                font=(FONT, 13, "bold"), justify="left",
            ).pack(anchor="w", pady=(2, 0))

        field("LARGURA", self.raw_in_width)
        field("ALTURA", self.raw_in_height)

        col = ctk.CTkFrame(fields, fg_color="transparent")
        col.pack(side="left", padx=(0, 26))
        ctk.CTkLabel(
            col, text="FORMATO DE PIXEL", text_color=COL_SUB, font=(FONT, 11, "bold"),
        ).pack(anchor="w")
        self._option_menu(col, self.raw_in_pix_fmt, 100).pack(anchor="w", pady=(2, 0))

        return outer

    def _build_format_row(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=26, pady=(0, 14))
        row.grid_columnconfigure(0, weight=1)

        col = ctk.CTkFrame(row, fg_color="transparent")
        col.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            col, text="CONVERTER PARA", text_color=COL_SUB, font=(FONT, 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        seg = ctk.CTkFrame(col, fg_color=COL_CARD, corner_radius=10)
        seg.pack(anchor="w")
        for fmt in iu.OUTPUT_FORMATS:
            btn = ctk.CTkButton(
                seg, text=fmt.upper(), width=52, height=34, corner_radius=7,
                font=(FONT, 12, "bold"), command=lambda f=fmt: self._select_format(f),
            )
            btn.pack(side="left", padx=1, pady=4)
            self._format_buttons[fmt] = btn
        self._style_format_buttons()

    def _style_format_buttons(self):
        selected = self.output_format.get()
        for fmt, btn in self._format_buttons.items():
            if fmt == selected:
                btn.configure(fg_color=COL_AMBER, text_color=COL_AMBER_TEXT, hover_color=COL_AMBER)
            else:
                btn.configure(fg_color="transparent", text_color=COL_SUB, hover_color=COL_CARD_ALT)

    def _select_format(self, fmt):
        self.output_format.set(fmt)
        self._style_format_buttons()
        self._refresh_visibility()

    def _build_raw_out_panel(self):
        outer = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12)
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(padx=14, pady=10, anchor="w")
        ctk.CTkLabel(
            inner, text="FORMATO DE PIXEL (SAÍDA .RAW)", text_color=COL_SUB,
            font=(FONT, 11, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        line = ctk.CTkFrame(inner, fg_color="transparent")
        line.pack(anchor="w")
        self._option_menu(line, self.raw_out_pix_fmt, 120).pack(side="left")
        ctk.CTkLabel(
            line, text="   as dimensões vão no nome do arquivo, para ler de volta sem digitar nada",
            text_color=COL_MUTED, font=(FONT, 12),
        ).pack(side="left")
        return outer

    def _build_quality_panel(self):
        outer = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12)
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(padx=14, pady=10, fill="x")
        ctk.CTkLabel(
            inner, text="QUALIDADE", text_color=COL_SUB, font=(FONT, 11, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        line = ctk.CTkFrame(inner, fg_color="transparent")
        line.pack(fill="x")
        value_label = ctk.CTkLabel(
            line, text=str(self.quality.get()), width=34, text_color=COL_AMBER,
            font=(FONT, 14, "bold"),
        )
        ctk.CTkSlider(
            line, from_=1, to=100, number_of_steps=99, variable=self.quality,
            width=300, progress_color=COL_AMBER, button_color=COL_AMBER,
            button_hover_color="#c69643", fg_color=COL_TRACK,
            command=lambda v: value_label.configure(text=str(int(v))),
        ).pack(side="left")
        value_label.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            line, text="  menor = arquivo menor, mais perda", text_color=COL_MUTED,
            font=(FONT, 12),
        ).pack(side="left")
        return outer

    def _build_dest_row(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=26, pady=(0, 16))
        ctk.CTkLabel(
            row, text="SALVAR EM", text_color=COL_SUB, font=(FONT, 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        dest = ctk.CTkFrame(row, fg_color=COL_CARD, corner_radius=10, height=42)
        dest.pack(fill="x")
        dest.pack_propagate(False)

        ctk.CTkLabel(dest, text="📁", text_color=COL_SUB, font=(FONT, 13)).pack(
            side="left", padx=(14, 8)
        )
        # os botões são empacotados antes do texto para nunca serem empurrados para fora por um caminho longo
        ctk.CTkButton(
            dest, text="Trocar", width=1, height=24, fg_color="transparent",
            hover_color=COL_CARD_ALT, text_color=COL_AMBER, font=(FONT, 12, "bold"),
            command=self._pick_output_dir,
        ).pack(side="right", padx=(4, 14))
        self.dest_label = ctk.CTkLabel(
            dest, text="", text_color=COL_DEST, font=(FONT, 13), anchor="w",
        )
        self.dest_label.pack(side="left", fill="x", expand=True)
        self.padrao_btn = ctk.CTkButton(
            dest, text="Usar pasta da imagem", width=1, height=24, fg_color="transparent",
            hover_color=COL_CARD_ALT, text_color=COL_SUB, font=(FONT, 12),
            command=self._usar_pasta_padrao,
        )
        self._refresh_dest()

    def _build_bottom_bar(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=6, column=0, sticky="ew", padx=26, pady=(0, 22))
        wrap.grid_columnconfigure(0, weight=1)

        # --- estado ocioso (fila vazia / pronta / concluída) ---
        self.bottom_idle = ctk.CTkFrame(wrap, fg_color=COL_CARD, corner_radius=12, height=56)
        self.bottom_idle.grid_propagate(False)
        self.bottom_idle.grid_columnconfigure(0, weight=1)
        self.idle_label = ctk.CTkLabel(
            self.bottom_idle, text="Nenhum arquivo na fila", text_color=COL_MUTED,
            font=(FONT, 12),
        )
        self.idle_label.grid(row=0, column=0, sticky="w", padx=16)
        self.convert_btn = ctk.CTkButton(
            self.bottom_idle, text="Converter tudo", fg_color=COL_AMBER,
            text_color=COL_AMBER_TEXT, hover_color="#c69643", corner_radius=10,
            height=42, width=150, font=(FONT, 14, "bold"), state="disabled",
            command=self._start_batch,
        )
        self.convert_btn.grid(row=0, column=1, sticky="e", padx=16)

        # --- estado convertendo ---
        self.bottom_run = ctk.CTkFrame(wrap, fg_color=COL_CARD, corner_radius=12)
        self.bottom_run.grid_columnconfigure(0, weight=1)

        ov = ctk.CTkFrame(self.bottom_run, fg_color="transparent")
        ov.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ov.grid_columnconfigure(0, weight=1)
        self.convert_label = ctk.CTkLabel(
            ov, text="Convertendo 0 de 0", text_color=COL_TEXT, font=(FONT, 13, "bold"),
            anchor="w",
        )
        self.convert_label.grid(row=0, column=0, sticky="w")
        self.overall_pct_label = ctk.CTkLabel(
            ov, text="0%", text_color=COL_AMBER, font=(FONT, 20, "bold"),
        )
        self.overall_pct_label.grid(row=0, column=1, sticky="e")

        self.overall_progress = ctk.CTkProgressBar(
            self.bottom_run, height=8, corner_radius=99,
            fg_color=COL_TRACK, progress_color=COL_AMBER,
        )
        self.overall_progress.set(0)
        self.overall_progress.grid(row=1, column=0, sticky="ew", padx=16)

        br = ctk.CTkFrame(self.bottom_run, fg_color="transparent")
        br.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 14))
        br.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(
            br, text="", text_color=COL_MUTED, font=(FONT, 12), anchor="w",
        )
        self.summary_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            br, text="Cancelar", fg_color=COL_CARD_ALT, hover_color=COL_BADGE_BG,
            text_color=COL_TEXT, corner_radius=10, height=36, width=110,
            font=(FONT, 13, "bold"), command=self._cancel_batch,
        ).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------- visibilidade
    def _refresh_visibility(self):
        fmt = self.output_format.get()

        if self._raw_sem_nome():
            self.raw_in_panel.grid(row=2, column=0, sticky="ew", padx=26, pady=(0, 14))
        else:
            self.raw_in_panel.grid_forget()

        self.raw_out_panel.grid_forget()
        self.quality_panel.grid_forget()
        if fmt == "raw":
            self.raw_out_panel.grid(row=4, column=0, sticky="ew", padx=26, pady=(0, 14))
        elif fmt in iu.LOSSY_FORMATS:
            self.quality_panel.grid(row=4, column=0, sticky="ew", padx=26, pady=(0, 14))

        if self.files:
            self.drop_zone.grid_forget()
            self.queue_frame.grid(row=0, column=0, sticky="nsew")
        else:
            self.queue_frame.grid_forget()
            self.drop_zone.grid(row=0, column=0, sticky="nsew")

    def _raw_sem_nome(self):
        """Há .raw na fila cujo nome não diz largura, altura e formato de pixel?"""
        return any(
            iu.is_raw(item["path"]) and iu.raw_params_from_name(item["path"]) is None
            for item in self.files
        )

    def _refresh_bottom_bar(self):
        if self._converting:
            self.bottom_idle.grid_forget()
            self.bottom_run.grid(row=0, column=0, sticky="ew")
        else:
            self.bottom_run.grid_forget()
            self.bottom_idle.grid(row=0, column=0, sticky="ew")
            self._refresh_idle_text()

    def _refresh_idle_text(self):
        total = len(self.files)
        if total == 0:
            self.idle_label.configure(text="Nenhum arquivo na fila")
            self.convert_btn.configure(state="disabled", fg_color=COL_CARD_ALT, text_color=COL_MUTED)
            return

        done = sum(1 for f in self.files if f["status"] == STATUS_CONCLUIDO)
        errors = sum(1 for f in self.files if f["status"] == STATUS_ERRO)
        restantes = total - done

        if restantes == 0:
            self.idle_label.configure(text=f"{done} concluído(s) · troque o formato ou adicione mais arquivos")
            self.convert_btn.configure(
                text="Converter tudo", state="disabled", fg_color=COL_CARD_ALT, text_color=COL_MUTED
            )
            return

        if done or errors:
            parts = []
            if done:
                parts.append(f"{done} concluído(s)")
            if errors:
                parts.append(f"{errors} com erro")
            self.idle_label.configure(text=" · ".join(parts))
        else:
            self.idle_label.configure(text=f"{total} arquivo(s) na fila")
        self.convert_btn.configure(
            text=f"Converter {restantes} restante(s)" if done else "Converter tudo",
            state="normal", fg_color=COL_AMBER, text_color=COL_AMBER_TEXT,
        )

    def _update_subtitle(self):
        if self.files:
            self.subtitle_label.configure(text=f"{len(self.files)} arquivos na fila")
        else:
            self.subtitle_label.configure(text="Converta várias imagens de uma vez")

    # --------------------------------------------------------- lista de arquivos
    def _add_files(self):
        padrao = " ".join(f"*{ext}" for ext in sorted(iu.INPUT_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Escolha as imagens",
            filetypes=[("Imagens", padrao), ("Todos os arquivos", "*.*")],
        )
        self._add_paths(paths)

    @staticmethod
    def _expandir(paths):
        """Arquivos soltos entram como estão; pastas entram com as imagens de dentro (e subpastas)."""
        for p in paths:
            if os.path.isdir(p):
                for raiz, pastas, nomes in os.walk(p):
                    pastas.sort()
                    for nome in sorted(nomes):
                        if iu.is_supported(nome):
                            yield os.path.join(raiz, nome)
            elif os.path.isfile(p):
                yield p

    def _add_paths(self, paths):
        paths = [os.path.normpath(p) for p in self._expandir(paths)]
        if not paths:
            return
        existing = {item["path"] for item in self.files}
        for path in paths:
            if path in existing:
                continue
            existing.add(path)
            item = {"path": path, "status": STATUS_PENDENTE, "log": "", "row": None}
            self.files.append(item)
            self._rows_pendentes.append(item)
        if not self.pasta_padrao:
            self.pasta_padrao = os.path.dirname(paths[0])
            self._refresh_dest()
        self._update_subtitle()
        self._refresh_visibility()
        self._refresh_bottom_bar()
        self._desenhar_linhas()

    def _desenhar_linhas(self):
        lote, self._rows_pendentes = self._rows_pendentes[:ROWS_PER_TICK], self._rows_pendentes[ROWS_PER_TICK:]
        for item in lote:
            if item.get("removido"):
                continue
            row = FileRow(self.queue_frame, item["path"], self._remove_file, self._view_log)
            row.pack(fill="x", padx=4, pady=2)
            row.set_status(item["status"])
            item["row"] = row
        if self._rows_pendentes:
            self.after(1, self._desenhar_linhas)

    def _remove_file(self, path):
        if self._converting:
            return
        for item in self.files:
            if item["path"] == path:
                item["removido"] = True
                if item["row"] is not None:
                    item["row"].destroy()
        self.files = [item for item in self.files if item["path"] != path]
        self._update_subtitle()
        self._refresh_visibility()
        self._refresh_bottom_bar()

    def _view_log(self, path):
        item = next((f for f in self.files if f["path"] == path), None)
        if not item or not item["log"]:
            messagebox.showinfo("Detalhes", "Sem detalhes de erro para este arquivo.")
            return
        tail = "\n".join(item["log"].strip().splitlines()[-25:])
        messagebox.showinfo(f"Erro: {os.path.basename(path)}", tail)

    def _set_item_status(self, path, status):
        for item in self.files:
            if item["path"] == path:
                item["status"] = status
                if item["row"] is not None:
                    item["row"].set_status(status)

    # -------------------------------------------------------------- ações
    def _pasta_saida(self):
        return self.pasta_escolhida or self.pasta_padrao

    @staticmethod
    def _abreviar(caminho, limite=60):
        if len(caminho) <= limite:
            return caminho
        return caminho[:18] + "…" + caminho[-(limite - 19):]

    def _refresh_dest(self):
        if self.pasta_escolhida:
            self.dest_label.configure(text=self._abreviar(self.pasta_escolhida, 48))
            self.padrao_btn.pack(side="right", before=self.dest_label)
        else:
            self.dest_label.configure(text=self._abreviar(self.pasta_padrao) or "Pasta da primeira imagem adicionada")
            self.padrao_btn.pack_forget()

    def _pick_output_dir(self):
        path = filedialog.askdirectory(title="Escolha a pasta de saída")
        if path:
            self.pasta_escolhida = os.path.normpath(path)
            self._refresh_dest()
            self._config_mudou()

    def _usar_pasta_padrao(self):
        self.pasta_escolhida = ""
        self._refresh_dest()
        self._config_mudou()

    def _start_batch(self):
        if self._converting:
            return
        items = [f for f in self.files if f["status"] != STATUS_CONCLUIDO]
        if not items:
            return
        out_dir = self._pasta_saida()
        if not out_dir:
            messagebox.showwarning("Atenção", "Escolha uma pasta de saída.")
            return

        raw_in = None
        if self._raw_sem_nome():
            try:
                raw_in = {
                    "width": int(self.raw_in_width.get()),
                    "height": int(self.raw_in_height.get()),
                    "pix_fmt": self.raw_in_pix_fmt.get(),
                }
                if raw_in["width"] <= 0 or raw_in["height"] <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Atenção", "Largura e altura do .raw devem ser números maiores que zero.")
                return

        out_format = self.output_format.get()
        raw_out = {"pix_fmt": self.raw_out_pix_fmt.get()} if out_format == "raw" else None

        for item in items:
            self._set_item_status(item["path"], STATUS_PENDENTE)
            item["log"] = ""

        salvar_config(self._config_atual())
        self._cancel_event.clear()
        self._converting = True
        self._config_mudou_durante_lote = False
        self.overall_progress.set(0)
        self.overall_pct_label.configure(text="0%")
        self.convert_label.configure(text=f"Convertendo · 0 de {len(items)} prontas")
        self.summary_label.configure(text="")
        self._refresh_bottom_bar()

        thread = threading.Thread(
            target=self._run_batch_thread,
            args=(items, out_dir, out_format, self.quality.get(), raw_in, raw_out),
            daemon=True,
        )
        thread.start()
        self._ler_avisos()

    def _cancel_batch(self):
        self._cancel_event.set()
        self.convert_label.configure(text="Cancelando... (terminando as que já começaram)")

    def _ui(self, func, *args):
        self._avisos.put((func, args))

    def _ler_avisos(self):
        while True:
            try:
                func, args = self._avisos.get_nowait()
            except queue.Empty:
                break
            func(*args)
        if self._converting:
            self.after(50, self._ler_avisos)

    def _run_batch_thread(self, items, out_dir, out_format, quality, raw_in, raw_out):
        total = len(items)
        used_names = set()
        lock = threading.Lock()
        contagem = {"ok": 0, "erro": 0, "prontas": 0}

        def caminho_saida(base_name, ext):
            with lock:
                return self._unique_output_path(base_name, ext, out_dir, used_names)

        def converter(item):
            path = item["path"]
            if self._cancel_event.is_set():
                self._ui(self._set_item_status, path, STATUS_CANCELADO)
                return
            self._ui(self._set_item_status, path, STATUS_CONVERTENDO)
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
            self._ui(self._item_terminado, path, status, prontas, total)

        with ThreadPoolExecutor(max_workers=iu.parallel_workers(out_format)) as pool:
            list(pool.map(converter, items))

        self._ui(self._on_batch_done, contagem["ok"], contagem["erro"], total)

    def _item_terminado(self, path, status, prontas, total):
        self._set_item_status(path, status)
        self.overall_progress.set(prontas / total)
        self.overall_pct_label.configure(text=f"{int(prontas / total * 100)}%")
        if not self._cancel_event.is_set():
            self.convert_label.configure(text=f"Convertendo · {prontas} de {total} prontas")

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
        self._converting = False

        if self._cancel_event.is_set():
            self.summary_label.configure(text=f"Cancelado. {done_count}/{total} concluídos antes de parar.")
        elif error_count:
            self.summary_label.configure(
                text=f"Concluído com erros: {done_count} ok, {error_count} com erro de {total}."
            )
        else:
            self.summary_label.configure(text=f"Concluído: {done_count}/{total} arquivos convertidos.")

        self._refresh_bottom_bar()
        if self._config_mudou_durante_lote:
            self._config_mudou()


if __name__ == "__main__":
    App().mainloop()
