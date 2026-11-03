"""Testes da fila e do lote (classe main.App), sem abrir janela: rodam lotes completos
de verdade, nas mesmas situações que a interface usa. O teste na janela real está em
.github/teste_e2e.py."""

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
        self.eventos = []
        self.app = self.novo_app()

    def tearDown(self):
        self.app.cancelar()
        self.esperar()
        main.CONFIG_PATH = self._config_original
        shutil.rmtree(self.dir, ignore_errors=True)

    def novo_app(self):
        return main.App(self.eventos.append)

    def imagens(self, n, pasta="", nome="foto"):
        destino = os.path.join(self.dir, pasta)
        os.makedirs(destino, exist_ok=True)
        paths = []
        for i in range(n):
            p = os.path.join(destino, f"{nome}{i if nome == 'foto' else ''}.png")
            Image.new("RGB", (64, 48), (i * 20 % 255, 80, 160)).save(p)
            paths.append(p)
        return paths

    def esperar(self, timeout=60):
        fim = time.time() + timeout
        while self.app._converting and time.time() < fim:
            time.sleep(0.01)
        self.assertFalse(self.app._converting, "o lote não terminou")

    def converter(self, timeout=60):
        self.app.pasta_escolhida = self.saida
        self.assertIsNone(self.app.converter())
        self.esperar(timeout)

    def status(self):
        return [f["status"] for f in self.app.files]

    def saidas(self):
        return sorted(os.listdir(self.saida))


class SoOQueFalta(AppBase):
    def test_segundo_clique_nao_reconverte(self):
        self.app.adicionar(self.imagens(3))
        self.converter()
        self.assertEqual(len(self.saidas()), 3)
        self.assertFalse(self.app._resumo()["habilitado"])
        self.converter()
        self.assertEqual(len(self.saidas()), 3)

    def test_arquivo_novo_converte_so_ele(self):
        self.app.adicionar(self.imagens(2))
        self.converter()
        self.app.adicionar(self.imagens(1, "outra", nome="nova"))
        self.assertEqual(self.app._resumo()["botao"], "Converter 1 restante")
        self.converter()
        self.assertEqual(self.saidas(), ["foto0_convertido.jpg", "foto1_convertido.jpg", "nova_convertido.jpg"])

    def test_trocar_formato_permite_converter_de_novo(self):
        self.app.adicionar(self.imagens(2))
        self.converter()
        self.app.definir_opcoes({"formato": "png"})
        self.assertEqual(self.status(), [main.STATUS_PENDENTE] * 2)
        self.converter()
        self.assertEqual(len([s for s in self.saidas() if s.endswith(".png")]), 2)

    def test_trocar_qualidade_tambem(self):
        self.app.adicionar(self.imagens(1))
        self.converter()
        self.app.definir_opcoes({"qualidade": 50})
        self.assertEqual(self.status(), [main.STATUS_PENDENTE])

    def test_arquivo_com_erro_e_tentado_de_novo(self):
        ruim = os.path.join(self.dir, "quebrado.png")
        open(ruim, "wb").write(b"isto nao e uma imagem")
        self.app.adicionar(self.imagens(2) + [ruim])
        self.converter()
        self.assertEqual(self.status().count(main.STATUS_ERRO), 1)
        self.assertIn("Traceback", self.app.ver_log(ruim))
        Image.new("RGB", (10, 10)).save(ruim)  # consertado
        self.converter()
        self.assertEqual(self.status(), [main.STATUS_CONCLUIDO] * 3)
        self.assertEqual(len(self.saidas()), 3)


class Fila(AppBase):
    def test_pasta_inteira_entra_com_subpastas(self):
        self.imagens(2, "album")
        self.imagens(1, os.path.join("album", "sub"), nome="dentro")
        open(os.path.join(self.dir, "album", "leia-me.txt"), "w").write("x")
        self.app.adicionar([os.path.join(self.dir, "album")])
        self.assertEqual(len(self.app.files), 3)

    def test_repetido_nao_entra_duas_vezes_e_remover(self):
        fotos = self.imagens(2)
        self.app.adicionar(fotos + fotos)
        self.assertEqual(len(self.app.files), 2)
        self.app.remover(fotos[0])
        self.assertEqual([f["path"] for f in self.app.files], [os.path.normpath(fotos[1])])

    def test_raw_sem_dimensoes_valida_parametros(self):
        raw = os.path.join(self.dir, "glitch.raw")
        open(raw, "wb").write(bytes(8 * 4 * 3))
        self.app.adicionar([raw])
        self.assertTrue(self.app._raw_sem_nome())
        self.app.definir_opcoes({"raw_in_largura": "abc"})
        self.app.pasta_escolhida = self.saida
        self.assertIsNotNone(self.app.converter())
        self.app.definir_opcoes({"raw_in_largura": "8", "raw_in_altura": "4"})
        self.converter()
        self.assertEqual(self.status(), [main.STATUS_CONCLUIDO])

    def test_interface_recebe_eventos(self):
        self.app.adicionar(self.imagens(2))
        self.converter()
        tipos = [e["tipo"] for e in self.eventos]
        for tipo in ("fila", "inicio", "item", "progresso", "fim"):
            self.assertIn(tipo, tipos)
        self.assertEqual(self.eventos[-1]["tipo"], "fim")


class Paralelo(AppBase):
    def test_nomes_iguais_em_paralelo_nao_se_sobrescrevem(self):
        paths = []
        for i in range(24):
            paths += self.imagens(1, f"pasta{i}", nome="igual")
        self.app.adicionar(paths)
        self.converter()
        self.assertEqual(len(self.saidas()), 24)
        self.assertEqual(self.status(), [main.STATUS_CONCLUIDO] * 24)

    def test_cancelar_nao_deixa_nada_pela_metade(self):
        self.app.adicionar(self.imagens(40))
        self.app.pasta_escolhida = self.saida
        self.app.converter()
        self.app.cancelar()
        self.esperar()
        st = self.status()
        self.assertNotIn(main.STATUS_CONVERTENDO, st)
        self.assertNotIn(main.STATUS_PENDENTE, st)
        self.assertEqual(st.count(main.STATUS_CONCLUIDO), len(self.saidas()))


class LembraEscolhas(AppBase):
    def test_escolhas_voltam_ao_reabrir(self):
        self.app.definir_opcoes({"formato": "webp", "qualidade": 70, "raw_out_pix_fmt": "bgra"})
        self.app.pasta_escolhida = self.saida
        self.app.fechar()
        self.app = self.novo_app()
        self.assertEqual(self.app.opcoes["formato"], "webp")
        self.assertEqual(self.app.opcoes["qualidade"], 70)
        self.assertEqual(self.app.opcoes["raw_out_pix_fmt"], "bgra")
        self.assertEqual(self.app._pasta_saida(), self.saida)

    def test_pasta_apagada_volta_ao_padrao(self):
        self.app.pasta_escolhida = os.path.join(self.dir, "sumiu")
        os.makedirs(self.app.pasta_escolhida)
        self.app.fechar()
        os.rmdir(os.path.join(self.dir, "sumiu"))
        self.app = self.novo_app()
        self.assertEqual(self.app.pasta_escolhida, "")

    def test_config_corrompida_usa_padroes(self):
        os.makedirs(os.path.dirname(main.CONFIG_PATH), exist_ok=True)
        with open(main.CONFIG_PATH, "w") as f:
            f.write("{nao e json")
        self.app = self.novo_app()
        self.assertEqual(self.app.opcoes["formato"], "jpg")

    def test_valores_invalidos_sao_ignorados(self):
        os.makedirs(os.path.dirname(main.CONFIG_PATH), exist_ok=True)
        with open(main.CONFIG_PATH, "w") as f:
            json.dump({"formato": "exe", "qualidade": "alta", "raw_out_pix_fmt": "xyz"}, f)
        self.app = self.novo_app()
        self.assertEqual(self.app.opcoes["formato"], "jpg")
        self.assertEqual(self.app.opcoes["qualidade"], 90)
        self.assertEqual(self.app.opcoes["raw_out_pix_fmt"], "rgb24")

    def test_valores_invalidos_da_tela_sao_ignorados(self):
        self.app.definir_opcoes({"formato": "exe", "qualidade": "muita", "raw_in_pix_fmt": "xyz"})
        self.assertEqual(self.app.opcoes["formato"], "jpg")
        self.assertEqual(self.app.opcoes["qualidade"], 90)
        self.assertEqual(self.app.opcoes["raw_in_pix_fmt"], "rgb24")


if __name__ == "__main__":
    unittest.main()
