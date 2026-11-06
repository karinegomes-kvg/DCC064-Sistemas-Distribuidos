import json
import unittest
from unittest.mock import Mock, patch

from atendimento import atender_consultas
from controle_carga import ControleCarga


class TestesControleCarga(unittest.TestCase):
    def test_limite_por_ip_nao_consume_a_cota_dos_outros_clientes(self):
        controle = ControleCarga(limite_por_ip=2, limite_global=4)
        with patch("controle_carga.time.monotonic", return_value=0.0):
            self.assertTrue(controle.aceitar("192.0.2.1"))
            self.assertTrue(controle.aceitar("192.0.2.1"))
            self.assertFalse(controle.aceitar("192.0.2.1"))
            self.assertTrue(controle.aceitar("192.0.2.2"))
            self.assertTrue(controle.aceitar("192.0.2.2"))
        self.assertEqual(controle.descartados_por_ip, 1)
        self.assertEqual(controle.descartados_globais, 0)
        self.assertEqual(len(controle.recentes), 4)

    def test_limite_global_conta_todas_as_origens(self):
        controle = ControleCarga(limite_por_ip=2, limite_global=3)
        with patch("controle_carga.time.monotonic", return_value=0.0):
            for ip in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):
                self.assertTrue(controle.aceitar(ip))
            self.assertFalse(controle.aceitar("192.0.2.4"))
            self.assertFalse(controle.aceitar("192.0.2.1"))
        self.assertEqual(controle.descartados_globais, 2)
        self.assertEqual(len(controle.recentes), 3)

    def test_janela_movel_nao_renova_cota_na_virada_do_segundo(self):
        controle = ControleCarga(limite_por_ip=2, limite_global=10)
        with patch("controle_carga.time.monotonic", return_value=0.75) as relogio:
            self.assertTrue(controle.aceitar("192.0.2.1"))
            self.assertTrue(controle.aceitar("192.0.2.1"))
            relogio.return_value = 1.0
            self.assertFalse(controle.aceitar("192.0.2.1"))
            relogio.return_value = 1.75
            self.assertTrue(controle.aceitar("192.0.2.1"))
        self.assertEqual(controle.por_ip, {"192.0.2.1": 1})

    def test_expiracao_remove_somente_admissoes_com_um_segundo(self):
        controle = ControleCarga(limite_por_ip=2, limite_global=3)
        with patch("controle_carga.time.monotonic", return_value=0.0) as relogio:
            self.assertTrue(controle.aceitar("192.0.2.1"))
            relogio.return_value = 0.5
            self.assertTrue(controle.aceitar("192.0.2.1"))
            self.assertTrue(controle.aceitar("192.0.2.2"))
            relogio.return_value = 1.0
            self.assertTrue(controle.aceitar("192.0.2.3"))
            self.assertEqual(controle.por_ip,
                             {"192.0.2.1": 1, "192.0.2.2": 1, "192.0.2.3": 1})
            relogio.return_value = 1.5
            self.assertTrue(controle.aceitar("192.0.2.1"))
        self.assertEqual(controle.por_ip, {"192.0.2.3": 1, "192.0.2.1": 1})

    def test_muitas_origens_recusadas_nao_aumentam_o_historico(self):
        controle = ControleCarga(limite_por_ip=1, limite_global=2)
        with patch("controle_carga.time.monotonic", return_value=0.0) as relogio:
            self.assertTrue(controle.aceitar("192.0.2.1"))
            self.assertTrue(controle.aceitar("192.0.2.2"))
            for indice in range(1000):
                self.assertFalse(controle.aceitar(f"198.51.{indice // 256}.{indice % 256}"))
            self.assertEqual(len(controle.recentes), 2)
            self.assertEqual(len(controle.por_ip), 2)
            relogio.return_value = 1.0
            self.assertTrue(controle.aceitar("192.0.2.3"))
        self.assertEqual(controle.por_ip, {"192.0.2.3": 1})
        self.assertEqual(controle.descartados_globais, 1000)

    def test_limites_precisam_ser_inteiros_positivos(self):
        for valor in (0, -1, True, False, 1.5, "20", None):
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    ControleCarga(valor, 100)
                with self.assertRaises(ValueError):
                    ControleCarga(20, valor)


class TestesLimitesAtendimento(unittest.TestCase):
    def setUp(self):
        self.substituicao_print = patch("builtins.print")
        self.imprimir = self.substituicao_print.start()
        self.addCleanup(self.substituicao_print.stop)

    def pacote(self, identificador, ip, porta):
        consulta = {"transaction_id": identificador, "qname": "teste.br",
                    "qtype": "A", "rd": False}
        return json.dumps(consulta).encode("utf-8"), (ip, porta)

    def processar(self, consulta):
        return {"transaction_id": consulta["transaction_id"], "status": "NXDOMAIN",
                "answer": [], "authority": []}

    def ids_respondidos(self, socket_teste):
        ids = []
        for chamada in socket_teste.sendto.call_args_list:
            ids.append(json.loads(chamada.args[0].decode("utf-8"))["transaction_id"])
        return ids

    def test_limita_antes_do_json_incluindo_malformados_e_troca_de_porta(self):
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            (b"\xff", ("192.0.2.1", 40000)),
            self.pacote(1, "192.0.2.1", 40001),
            self.pacote(2, "192.0.2.2", 40002),
            self.pacote(3, "192.0.2.3", 40003),
            (b"{", ("192.0.2.4", 40004)),
            self.pacote(4, "192.0.2.2", 40005),
            KeyboardInterrupt,
        ]
        with patch("controle_carga.time.monotonic", return_value=0.0), \
                patch("atendimento.json.loads", wraps=json.loads) as interpretar:
            with self.assertRaises(KeyboardInterrupt):
                atender_consultas(socket_teste, self.processar, "TESTE", trabalhadores=1,
                                  limite_por_ip=1, limite_global=3)
        self.assertEqual(interpretar.call_count, 2)
        self.assertEqual(self.ids_respondidos(socket_teste), [2, 3])
        avisos = [chamada for chamada in self.imprimir.call_args_list
                  if "Limite de carga:" in chamada.args[0]]
        self.assertEqual(len(avisos), 1)
        self.assertTrue(any("por IP=1, globais=2" in chamada.args[0]
                            for chamada in self.imprimir.call_args_list))

    def test_configuracao_do_ambiente_e_recuperacao_apos_um_segundo(self):
        socket_teste = Mock()
        pacotes = iter([
            (0.0, self.pacote(1, "192.0.2.1", 40000)),
            (0.0, self.pacote(2, "192.0.2.1", 40001)),
            (0.5, self.pacote(3, "192.0.2.1", 40002)),
            (1.0, self.pacote(4, "192.0.2.1", 40003)),
            (1.0, self.pacote(5, "192.0.2.1", 40004)),
        ])
        with patch("controle_carga.time.monotonic", return_value=0.0) as relogio:
            def receber(tamanho):
                try:
                    instante, pacote = next(pacotes)
                except StopIteration:
                    raise KeyboardInterrupt
                relogio.return_value = instante
                return pacote

            socket_teste.recvfrom.side_effect = receber
            with patch.dict("os.environ", {"LIMITE_POR_IP": "1", "LIMITE_GLOBAL": "2"}):
                with self.assertRaises(KeyboardInterrupt):
                    atender_consultas(socket_teste, self.processar, "TESTE", trabalhadores=1)
        self.assertEqual(self.ids_respondidos(socket_teste), [1, 4])
        avisos = [chamada for chamada in self.imprimir.call_args_list
                  if "Limite de carga:" in chamada.args[0]]
        self.assertEqual(len(avisos), 2)

    def test_parametros_explicitos_sobrescrevem_o_ambiente(self):
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            self.pacote(1, "192.0.2.1", 40000),
            self.pacote(2, "192.0.2.1", 40001),
            self.pacote(3, "192.0.2.2", 40002),
            KeyboardInterrupt,
        ]
        with patch("controle_carga.time.monotonic", return_value=0.0), \
                patch.dict("os.environ", {"LIMITE_POR_IP": "1", "LIMITE_GLOBAL": "2"}):
            with self.assertRaises(KeyboardInterrupt):
                atender_consultas(socket_teste, self.processar, "TESTE", trabalhadores=1,
                                  limite_por_ip=2, limite_global=3)
        self.assertEqual(self.ids_respondidos(socket_teste), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
