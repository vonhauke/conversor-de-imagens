import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageCms, ImageDraw  # noqa: E402

import image_utils as iu  # noqa: E402


def amostra(size=(120, 80)):
    im = Image.new("RGB", size, (30, 60, 200))
    ImageDraw.Draw(im).ellipse((20, 10, 90, 70), fill=(220, 160, 40))
    return im


def com_alpha():
    im = amostra().convert("RGBA")
    im.putalpha(Image.linear_gradient("L").resize(im.size))
    return im


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def caminho(self, nome):
        return os.path.join(self.dir, nome)

    def converter(self, origem, fmt, nome="saida", **kw):
        return iu.convert(origem, fmt, lambda ext: self.caminho(nome + ext), **kw)

    def abrir(self, path):
        if path.endswith(".raw"):
            return iu._open_raw(path, None)
        im = Image.open(path)
        im.load()
        self.addCleanup(im.close)
        return im


class TodasAsCombinacoes(Base):
    """Cada tipo de entrada vira cada formato de saída e o resultado abre de novo."""

    def test_matriz(self):
        rgb, rgba = amostra(), com_alpha()
        entradas = {
            "rgb.jpg": lambda p: rgb.save(p),
            "alpha.png": lambda p: rgba.save(p),
            "cmyk.jpg": lambda p: rgb.convert("CMYK").save(p),
            "cinza.pgm": lambda p: rgb.convert("L").save(p),
            "paleta.gif": lambda p: rgb.convert("P").save(p),
            "foto.heic": lambda p: rgb.save(p),
            "foto.avif": lambda p: rgba.save(p),
            "img.webp": lambda p: rgb.save(p),
            "img.tga": lambda p: rgb.save(p),
            "img.tiff": lambda p: rgb.save(p),
            "img.bmp": lambda p: rgb.save(p),
            "img.ico": lambda p: rgba.save(p),
            "16bits.png": lambda p: Image.new("I", (32, 32), 40000).convert("I;16").save(p),
            "solto.raw": lambda p: open(p, "wb").write(rgb.tobytes()),
        }
        for nome, criar in entradas.items():
            criar(self.caminho(nome))
        raw_in = {"width": 120, "height": 80, "pix_fmt": "rgb24"}
        for nome in entradas:
            for fmt in iu.OUTPUT_FORMATS:
                pixels = iu.RAW_PIXEL_FORMATS if fmt == "raw" else [None]
                for pix in pixels:
                    with self.subTest(entrada=nome, saida=fmt, pix=pix):
                        saida = f"{nome}_{fmt}_{pix}"
                        p = self.converter(
                            self.caminho(nome), fmt, saida, raw_in=raw_in,
                            raw_out={"pix_fmt": pix} if pix else None,
                        )
                        self.assertGreater(self.abrir(p).width, 0)


class Correcoes(Base):
    def test_transparencia_vira_branco_no_jpg(self):
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(self.caminho("t.png"))
        p = self.converter(self.caminho("t.png"), "jpg")
        r, g, b = self.abrir(p).getpixel((5, 5))
        self.assertTrue(min(r, g, b) > 245)

    def test_foto_deitada_e_endireitada(self):
        ex = Image.Exif()
        ex[0x0112] = 6
        amostra().save(self.caminho("d.jpg"), exif=ex.tobytes())
        for fmt in ("jpg", "png", "webp", "heic"):
            with self.subTest(fmt=fmt):
                self.assertEqual(self.abrir(self.converter(self.caminho("d.jpg"), fmt, fmt)).size, (80, 120))

    def test_jpg_mpo_de_celular_nao_vira_animacao(self):
        ex = Image.Exif()
        ex[0x0112] = 6
        amostra().save(self.caminho("m.jpg"), "MPO", save_all=True,
                       append_images=[Image.new("RGB", (60, 40))], exif=ex.tobytes())
        for fmt in ("png", "gif", "tiff", "avif"):
            with self.subTest(fmt=fmt):
                out = self.abrir(self.converter(self.caminho("m.jpg"), fmt, fmt))
                self.assertEqual(getattr(out, "n_frames", 1), 1)
                self.assertEqual(out.size, (80, 120))

    def test_animacao_mantida(self):
        quadros = [amostra().rotate(a) for a in (0, 30, 60)]
        quadros[0].save(self.caminho("a.gif"), save_all=True, append_images=quadros[1:], duration=100, loop=0)
        for fmt in iu.MULTIFRAME_FORMATS:
            with self.subTest(fmt=fmt):
                self.assertEqual(self.abrir(self.converter(self.caminho("a.gif"), fmt, fmt)).n_frames, 3)

    def test_falha_na_animacao_nao_deixa_arquivo_quebrado(self):
        quadros = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
        quadros[0].save(self.caminho("a.gif"), save_all=True, append_images=quadros[1:])
        original = Image.Image.save

        def falha(img, fp, format=None, **kw):
            if kw.get("save_all"):
                open(fp, "wb").write(b"lixo")
                raise OSError("simulado")
            return original(img, fp, format, **kw)

        pedidos = []

        def caminho(ext):
            pedidos.append(ext)
            return self.caminho(f"saida{len(pedidos)}{ext}")

        Image.Image.save = falha
        try:
            p = iu.convert(self.caminho("a.gif"), "webp", caminho)
        finally:
            Image.Image.save = original
        self.assertEqual(len(pedidos), 1)
        self.assertEqual(self.abrir(p).size, (20, 20))

    def test_16_bits_escuro_continua_escuro(self):
        Image.new("I", (10, 10), 300).convert("I;16").save(self.caminho("e.png"))
        self.assertLess(self.abrir(self.converter(self.caminho("e.png"), "jpg")).getpixel((5, 5)), 5)

    def test_perfil_de_cor_incompativel_nao_e_copiado(self):
        perfil = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
        Image.new("L", (10, 10), 128).save(self.caminho("g.png"), icc_profile=perfil)
        out = self.abrir(self.converter(self.caminho("g.png"), "webp"))
        self.assertEqual(out.mode, "RGB")
        self.assertFalse(out.info.get("icc_profile"))

    def test_perfil_de_cor_compativel_e_copiado(self):
        perfil = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        amostra().save(self.caminho("s.jpg"), icc_profile=perfil)
        self.assertEqual(self.abrir(self.converter(self.caminho("s.jpg"), "png")).info.get("icc_profile"), perfil)


class Raw(Base):
    def test_ida_e_volta_com_dimensoes_no_nome(self):
        original = com_alpha()
        original.save(self.caminho("o.png"))
        for pix in iu.RAW_PIXEL_FORMATS:
            with self.subTest(pix=pix):
                raw = self.converter(self.caminho("o.png"), "raw", pix, raw_out={"pix_fmt": pix})
                self.assertTrue(raw.endswith(f"_120x80_{pix}.raw"))
                volta = self.abrir(self.converter(raw, "png", pix + "_volta"))
                self.assertEqual(volta.size, (120, 80))
                if pix in ("rgba", "bgra"):
                    self.assertEqual(volta.tobytes(), original.tobytes())

    def test_nome_com_varias_dimensoes_usa_a_ultima(self):
        self.assertEqual(iu.raw_params_from_name("a_10x10_rgb24_convertido_20x30_gray.raw"), (20, 30, "gray"))
        self.assertIsNone(iu.raw_params_from_name("foto.raw"))

    def test_arquivo_menor_e_completado_com_preto(self):
        open(self.caminho("c_10x10_rgb24.raw"), "wb").write(b"\xff" * 150)
        im = iu._open_raw(self.caminho("c_10x10_rgb24.raw"), None)
        self.assertEqual(im.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(im.getpixel((9, 9)), (0, 0, 0))

    def test_dimensoes_absurdas_dao_erro(self):
        open(self.caminho("x.raw"), "wb").write(b"\0" * 100)
        with self.assertRaises(ValueError):
            iu._open_raw(self.caminho("x.raw"), {"width": 60000, "height": 60000, "pix_fmt": "rgba"})

    def test_sem_nome_e_sem_parametros_da_erro(self):
        open(self.caminho("y.raw"), "wb").write(b"\0" * 100)
        with self.assertRaises(ValueError):
            iu._open_raw(self.caminho("y.raw"), None)


class Paralelismo(unittest.TestCase):
    def test_heic_e_avif_usam_poucas_conversoes_simultaneas(self):
        self.assertEqual(iu.parallel_workers("heic"), 2)
        self.assertEqual(iu.parallel_workers("avif"), 2)
        self.assertTrue(1 <= iu.parallel_workers("jpg") <= 8)


if __name__ == "__main__":
    unittest.main()
