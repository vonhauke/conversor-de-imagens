"""Testes da janela: abrem o app de verdade (escondido) e rodam lotes completos."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import main  # noqa: E402


class AppBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saida = os.path.join(self.dir, "saida")
        os.makedirs(self.saida)
        self._config_original = main.CONFIG_PATH
        main.CONFIG_PATH = os.path.join(self.dir, "config", "config.json")
        self.app = self.novo_app()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        main.CONFIG_PATH = self._config_original
        shutil.rmtree(self.dir, ignore_errors=True)

    def novo_app(self):
        app = main.App()
        app.withdraw()
        return app

    def imagens(self, n, pasta="", nome="foto"):
        destino = os.path.join(self.dir, pasta)
        os.makedirs(destino, exist_ok=True)
        paths = []
        for i in range(n):
            p = os.path.join(destino, f"{nome}{i if nome == 'foto' else ''}.png")
            Image.new("RGB", (64, 48), (i * 20 % 255, 80, 160)).save(p)
            paths.append(p)
        return paths

    def converter(self, timeout=60):
        self.app.pasta_escolhida = self.saida
        self.app._start_batch()
        fim = time.time() + timeout
        while self.app._converting and time.time() < fim:
            self.app.update()
            time.sleep(0.01)
        self.app.update()
        self.assertFalse(self.app._converting, "o lote não terminou")

    def status(self):
        return [f["status"] for f in self.app.files]

    def saidas(self):
        return sorted(os.listdir(self.saida))


class SoOQueFalta(AppBase):
    def test_segundo_clique_nao_reconverte(self):
        self.app._add_paths(self.imagens(3))
        self.converter()
        self.assertEqual(len(self.saidas()), 3)
        self.assertEqual(self.app.convert_btn.cget("state"), "disabled")
        self.converter()
        self.assertEqual(len(self.saidas()), 3)

    def test_arquivo_novo_converte_so_ele(self):
        self.app._add_paths(self.imagens(2))
        self.converter()
        self.app._add_paths(self.imagens(1, "outra", nome="nova"))
        self.assertEqual(self.app.convert_btn.cget("text"), "Converter 1 restante(s)")
        self.converter()
        self.assertEqual(self.saidas(), ["foto0_convertido.jpg", "foto1_convertido.jpg", "nova_convertido.jpg"])

    def test_trocar_formato_permite_converter_de_novo(self):
        self.app._add_paths(self.imagens(2))
        self.converter()
        self.app._select_format("png")
        self.assertEqual(self.status(), [main.STATUS_PENDENTE] * 2)
        self.converter()
        self.assertEqual(len([s for s in self.saidas() if s.endswith(".png")]), 2)

    def test_trocar_qualidade_tambem(self):
        self.app._add_paths(self.imagens(1))
        self.converter()
        self.app.quality.set(50)
        self.assertEqual(self.status(), [main.STATUS_PENDENTE])

    def test_arquivo_com_erro_e_tentado_de_novo(self):
        ruim = os.path.join(self.dir, "quebrado.png")
        open(ruim, "wb").write(b"isto nao e uma imagem")
        self.app._add_paths(self.imagens(2) + [ruim])
        self.converter()
        self.assertEqual(self.status().count(main.STATUS_ERRO), 1)
        Image.new("RGB", (10, 10)).save(ruim)  # consertado
        self.converter()
        self.assertEqual(self.status(), [main.STATUS_CONCLUIDO] * 3)
        self.assertEqual(len(self.saidas()), 3)


class Paralelo(AppBase):
    def test_nomes_iguais_em_paralelo_nao_se_sobrescrevem(self):
        paths = []
        for i in range(24):
            paths += self.imagens(1, f"pasta{i}", nome="igual")
        self.app._add_paths(paths)
        self.converter()
        self.assertEqual(len(self.saidas()), 24)
        self.assertEqual(self.status(), [main.STATUS_CONCLUIDO] * 24)

    def test_cancelar_nao_deixa_nada_pela_metade(self):
        self.app._add_paths(self.imagens(40))
        self.app.pasta_escolhida = self.saida
        self.app._start_batch()
        self.app._cancel_batch()
        fim = time.time() + 60
        while self.app._converting and time.time() < fim:
            self.app.update()
            time.sleep(0.01)
        self.app.update()
        st = self.status()
        self.assertNotIn(main.STATUS_CONVERTENDO, st)
        self.assertNotIn(main.STATUS_PENDENTE, st)
        self.assertEqual(st.count(main.STATUS_CONCLUIDO), len(self.saidas()))


class LembraEscolhas(AppBase):
    def test_escolhas_voltam_ao_reabrir(self):
        self.app._select_format("webp")
        self.app.quality.set(70)
        self.app.raw_out_pix_fmt.set("bgra")
        self.app.pasta_escolhida = self.saida
        self.app._fechar()
        self.app = self.novo_app()
        self.assertEqual(self.app.output_format.get(), "webp")
        self.assertEqual(self.app.quality.get(), 70)
        self.assertEqual(self.app.raw_out_pix_fmt.get(), "bgra")
        self.assertEqual(self.app._pasta_saida(), self.saida)

    def test_pasta_apagada_volta_ao_padrao(self):
        self.app.pasta_escolhida = os.path.join(self.dir, "sumiu")
        os.makedirs(self.app.pasta_escolhida)
        self.app._fechar()
        os.rmdir(os.path.join(self.dir, "sumiu"))
        self.app = self.novo_app()
        self.assertEqual(self.app.pasta_escolhida, "")

    def test_config_corrompida_usa_padroes(self):
        os.makedirs(os.path.dirname(main.CONFIG_PATH), exist_ok=True)
        with open(main.CONFIG_PATH, "w") as f:
            f.write("{nao e json")
        self.app.destroy()
        self.app = self.novo_app()
        self.assertEqual(self.app.output_format.get(), "jpg")

    def test_valores_invalidos_sao_ignorados(self):
        os.makedirs(os.path.dirname(main.CONFIG_PATH), exist_ok=True)
        with open(main.CONFIG_PATH, "w") as f:
            json.dump({"formato": "exe", "qualidade": "alta", "raw_out_pix_fmt": "xyz"}, f)
        self.app.destroy()
        self.app = self.novo_app()
        self.assertEqual(self.app.output_format.get(), "jpg")
        self.assertEqual(self.app.quality.get(), 90)
        self.assertEqual(self.app.raw_out_pix_fmt.get(), "rgb24")


if __name__ == "__main__":
    unittest.main()
