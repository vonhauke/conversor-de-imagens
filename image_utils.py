"""Conversão de imagens com a Pillow (e pillow-heif para HEIC/HEIF), incluindo .raw
(pixels puros sem cabeçalho, usado em fluxos de glitch art / databending).
"""

import os
import re

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False

OUTPUT_FORMATS = ["jpg", "png", "webp", "gif", "bmp", "tiff", "ico", "heic", "avif", "tga", "ppm", "raw"]

# Formatos com perda, em que o controle de qualidade faz diferença.
LOSSY_FORMATS = {"jpg", "webp", "heic", "avif"}

# Formatos que guardam várias imagens (animação / páginas) num arquivo só.
MULTIFRAME_FORMATS = {"gif", "webp", "png", "tiff", "avif"}

INPUT_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".webp", ".gif", ".bmp", ".dib",
    ".tif", ".tiff", ".ico", ".heic", ".heif", ".avif", ".tga", ".ppm", ".pgm", ".pbm",
    ".pnm", ".raw",
}

PIL_FORMAT = {
    "jpg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
    "ico": "ICO", "heic": "HEIF", "avif": "AVIF", "tga": "TGA", "ppm": "PPM",
}

# formato de pixel do .raw -> (modo da Pillow, rawmode, bytes por pixel)
RAW_MODES = {
    "rgb24": ("RGB", "RGB", 3),
    "bgr24": ("RGB", "BGR", 3),
    "rgba": ("RGBA", "RGBA", 4),
    "bgra": ("RGBA", "BGRA", 4),
    "gray": ("L", "L", 1),
}
RAW_PIXEL_FORMATS = list(RAW_MODES)

# Os .raw gerados levam no nome as dimensões e o formato de pixel (ex.: foto_1920x1080_rgb24.raw),
# para poderem ser lidos de volta depois do databending sem precisar digitar nada.
_RAW_TAG_RE = re.compile(r"(\d+)x(\d+)_(" + "|".join(RAW_PIXEL_FORMATS) + r")(?![a-z0-9])", re.I)

# Os codificadores de HEIC e AVIF já usam quase todos os núcleos numa imagem só: converter
# muitas ao mesmo tempo não acelera, só gasta memória.
_SELF_THREADED = {"heic", "avif"}


def parallel_workers(fmt):
    """Quantas imagens converter ao mesmo tempo para o formato de destino."""
    if fmt in _SELF_THREADED:
        return 2
    return max(1, min(8, (os.cpu_count() or 2) - 1))


ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def is_raw(path):
    return os.path.splitext(path)[1].lower() == ".raw"


def is_supported(path):
    return os.path.splitext(path)[1].lower() in INPUT_EXTENSIONS


def raw_params_from_name(path):
    """(largura, altura, formato de pixel) lidos do nome do arquivo, ou None."""
    matches = _RAW_TAG_RE.findall(os.path.basename(path))
    if not matches:
        return None
    w, h, fmt = matches[-1]
    return int(w), int(h), fmt.lower()


def _open_raw(path, raw_in):
    params = raw_params_from_name(path)
    if params is None:
        if raw_in is None:
            raise ValueError("Informe largura, altura e formato de pixel do .raw.")
        params = raw_in["width"], raw_in["height"], raw_in["pix_fmt"]
    width, height, pix_fmt = params
    if width * height > 2 * Image.MAX_IMAGE_PIXELS:
        raise ValueError(f"{width}x{height} é grande demais; confira largura e altura do .raw.")
    mode, rawmode, bpp = RAW_MODES[pix_fmt]
    need = width * height * bpp
    with open(path, "rb") as f:
        data = f.read(need)
    # Depois de editado num programa de áudio o arquivo pode ter mudado de tamanho:
    # completa com preto o que faltar em vez de recusar.
    data = data.ljust(need, b"\0")
    return Image.frombytes(mode, (width, height), data, "raw", rawmode)


# ----------------------------------------------------------- modos de cor
def _has_alpha(img):
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in img.info


def _to_8bit(img):
    """Imagens de 16/32 bits ou ponto flutuante viram 8 bits, reescalando a faixa de valores."""
    if img.mode.startswith("I;16"):
        # faixa conhecida (0-65535): escala fixa, para uma imagem escura não virar branca
        return img.convert("I").point(lambda v: v / 256).convert("L")
    if img.mode in ("I", "F"):
        _, hi = img.getextrema()
        if hi and hi > 255:
            img = img.point(lambda v: v * (255.0 / hi))
        img = img.convert("L")
    return img


def _rgb_or_rgba(img):
    img = _to_8bit(img)
    if _has_alpha(img):
        return img if img.mode == "RGBA" else img.convert("RGBA")
    return img if img.mode == "RGB" else img.convert("RGB")


def _flatten(img, keep_gray=False):
    """Sem transparência: o que for transparente vira fundo branco."""
    img = _to_8bit(img)
    if keep_gray and img.mode in ("1", "L"):
        return img
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img if img.mode == "RGB" else img.convert("RGB")


def _prepare(img, fmt, raw_out=None):
    if fmt in ("jpg", "ppm"):
        return _flatten(img, keep_gray=True)
    if fmt == "png":
        return img if img.mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16") else _rgb_or_rgba(img)
    if fmt in ("bmp", "tga", "gif"):
        allowed = {"1", "L", "P", "RGB", "RGBA"} | ({"LA"} if fmt == "tga" else set())
        return img if img.mode in allowed else _rgb_or_rgba(img)
    if fmt == "tiff":
        return img if img.mode not in ("PA", "RGBa", "La") else _rgb_or_rgba(img)
    if fmt == "ico":
        img = _rgb_or_rgba(img).convert("RGBA")
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        return square.resize((256, 256), Image.LANCZOS) if side != 256 else square
    if fmt == "raw":
        mode = RAW_MODES[raw_out["pix_fmt"]][0]
        if mode == "L":
            return _flatten(img).convert("L")
        if mode == "RGB":
            return _flatten(img)
        return _rgb_or_rgba(img).convert("RGBA")
    return _rgb_or_rgba(img)  # webp, heic, avif


# ----------------------------------------------------------- gravação
def output_extension(fmt, size=None, raw_out=None):
    if fmt == "raw":
        return f"_{size[0]}x{size[1]}_{raw_out['pix_fmt']}.raw"
    return "." + fmt


def _save_kwargs(fmt, quality):
    if fmt == "jpg":
        return {"quality": quality, "optimize": True}
    if fmt == "webp":
        return {"quality": quality, "method": 4}
    if fmt in ("heic", "avif"):
        return {"quality": quality}
    if fmt == "tiff":
        return {"compression": "tiff_lzw"}
    if fmt == "ico":
        return {"sizes": ICO_SIZES}
    return {}


def _color_family(mode):
    if mode in ("1", "L", "LA", "La", "I", "F") or mode.startswith("I;16"):
        return "gray"
    if mode == "CMYK":
        return "cmyk"
    return "rgb" if mode in ("RGB", "RGBA", "RGBa", "P", "PA") else mode


def _metadata(source_mode, img, prepared_mode, fmt):
    """EXIF e perfil de cor, nos formatos que os guardam."""
    if fmt not in ("jpg", "png", "webp", "tiff", "heic", "avif"):
        return {}
    meta = {}
    exif = img.getexif()
    if exif:
        meta["exif"] = exif.tobytes()
    icc = img.info.get("icc_profile")
    # perfil de cinza ou CMYK numa imagem convertida para RGB (ou vice-versa) deixaria as cores erradas
    if icc and _color_family(source_mode) == _color_family(prepared_mode):
        meta["icc_profile"] = icc
    return meta


def _save(img, out_path, fmt, kwargs, meta):
    try:
        img.save(out_path, PIL_FORMAT[fmt], **kwargs, **meta)
    except Exception:
        if not meta:
            raise
        img.save(out_path, PIL_FORMAT[fmt], **kwargs)  # metadados problemáticos: grava sem eles


def _save_raw(img, out_path, pix_fmt):
    rawmode = RAW_MODES[pix_fmt][1]
    with open(out_path, "wb") as f:
        f.write(img.tobytes("raw", rawmode))


def convert(input_path, fmt, make_output_path, quality=90, raw_in=None, raw_out=None):
    """Converte um arquivo. make_output_path(extensão) devolve o caminho de saída a usar.

    Retorna o caminho gravado. Erros sobem como exceção, para a interface mostrar o detalhe.
    """
    if fmt == "heic" and not HEIF_OK:
        raise RuntimeError("Suporte a HEIC indisponível: instale com  pip install pillow-heif")

    if is_raw(input_path):
        return _convert_frame(_open_raw(input_path, raw_in), "RGB", fmt, make_output_path, quality, raw_out)

    with Image.open(input_path) as img:
        # MPO = JPG de celular/câmera com uma prévia ou mapa de profundidade junto: não é animação
        if getattr(img, "n_frames", 1) > 1 and img.format != "MPO" and fmt in MULTIFRAME_FORMATS:
            out_path = make_output_path(output_extension(fmt))
            try:
                kwargs = _save_kwargs(fmt, quality)
                if fmt == "gif":
                    kwargs["loop"] = img.info.get("loop", 0)
                img.save(out_path, PIL_FORMAT[fmt], save_all=True, **kwargs)
                return out_path
            except Exception:
                # não deu para manter a animação: grava só o primeiro quadro no mesmo arquivo
                if os.path.exists(out_path):
                    os.remove(out_path)
                img.seek(0)
                make_output_path = lambda _ext: out_path

        source_mode = img.mode
        frame = ImageOps.exif_transpose(img)  # fotos de celular deitadas ficam em pé
        return _convert_frame(frame, source_mode, fmt, make_output_path, quality, raw_out)


def _convert_frame(img, source_mode, fmt, make_output_path, quality, raw_out):
    prepared = _prepare(img, fmt, raw_out)
    meta = _metadata(source_mode, img, prepared.mode, fmt)
    out_path = make_output_path(output_extension(fmt, prepared.size, raw_out))
    if fmt == "raw":
        _save_raw(prepared, out_path, raw_out["pix_fmt"])
    else:
        _save(prepared, out_path, fmt, _save_kwargs(fmt, quality), meta)
    return out_path
