import json
import socket
from pathlib import Path
import sys
from threading import Event
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import client_test
import dns_server
import resolver
from atendimento import atender_consultas
from prazos import criar_prazo, tempo_restante
from validacao import consulta_valida


class TestesPrazos(unittest.TestCase):
    def setUp(self):
        resolver.cache.clear()
        self.addCleanup(resolver.cache.clear)
        substituicao_print = patch("builtins.print")
        substituicao_print.start()
        self.addCleanup(substituicao_print.stop)

    def consulta(self, identificador=1, rd=False):
        return {"transaction_id": identificador, "qname": "www.ufjf.br",
                "qtype": "A", "rd": rd}

    def resposta(self, identificador=1):
        return {"transaction_id": identificador, "status": "SUCCESS", "answer": [
            {"name": "www.ufjf.br", "type": "A", "value": "172.20.0.40", "ttl": 15}
        ], "authority": []}

    def encaminhamento(self, zona, ip):
        return {"transaction_id": 1, "status": "REFERRAL", "answer": [],
                "authority": [{"name": zona, "type": "NS", "value": ip, "ttl": 300}]}

    def test_orcamento_opcional_validado_e_limitado_ao_maximo(self):
        consulta = self.consulta()
        with patch("prazos.time.monotonic", return_value=100):
            self.assertEqual(criar_prazo(consulta), 105)
            for valor in (True, False, None, "2", [], {}, 0, -1,
                          float("nan"), float("inf"), float("-inf")):
                with self.subTest(valor=valor):
                    consulta["tempo_restante"] = valor
                    self.assertFalse(consulta_valida(consulta))
            for valor, esperado in ((2, 102), (0.5, 100.5), (10, 105), (10 ** 400, 105)):
                with self.subTest(valor=valor):
                    consulta["tempo_restante"] = valor
                    self.assertTrue(consulta_valida(consulta))
                    self.assertEqual(criar_prazo(consulta), esperado)
            with self.assertRaises(socket.timeout):
                tempo_restante(100)

    def test_iteracao_desconta_o_tempo_de_cada_servidor(self):
        socket_teste = Mock()
        respostas = iter([
            self.encaminhamento("br", "172.20.0.101"),
            self.encaminhamento("ufjf.br", "172.20.0.102"),
            self.resposta(),
        ])
        with patch("prazos.time.monotonic", return_value=100) as relogio:
            def receber(tamanho):
                relogio.return_value += 1
                endereco = socket_teste.sendto.call_args.args[1]
                return json.dumps(next(respostas)).encode("utf-8"), endereco

            socket_teste.recvfrom.side_effect = receber
            with patch("socket.socket", return_value=socket_teste), \
                    patch("resolver.secrets.randbits", return_value=1):
                resultado = resolver.resolver_consulta(self.consulta())
        orcamentos = [json.loads(chamada.args[0])["tempo_restante"]
                      for chamada in socket_teste.sendto.call_args_list]
        self.assertEqual(orcamentos, [5, 4, 3])
        self.assertEqual(resultado["status"], "SUCCESS")
        socket_teste.close.assert_called_once()

    def test_timeout_usa_prazo_total_e_nao_vira_nxdomain(self):
        socket_teste = Mock()
        with patch("prazos.time.monotonic", return_value=100) as relogio:
            def receber(tamanho):
                if socket_teste.recvfrom.call_count == 1:
                    relogio.return_value = 103
                    return (json.dumps(self.encaminhamento("br", "172.20.0.101")).encode(),
                            (resolver.ROOT_DNS_IP, 5300))
                relogio.return_value = 105
                raise socket.timeout

            socket_teste.recvfrom.side_effect = receber
            with patch("socket.socket", return_value=socket_teste), \
                    patch("resolver.secrets.randbits", return_value=1):
                resultado = resolver.resolver_consulta(self.consulta())
        orcamentos = [json.loads(chamada.args[0])["tempo_restante"]
                      for chamada in socket_teste.sendto.call_args_list]
        self.assertEqual(orcamentos, [5, 2])
        self.assertIsNone(resultado)
        self.assertEqual(resolver.cache, {})
        socket_teste.close.assert_called_once()

    def test_recursao_propagada_desconta_filas_dos_servidores(self):
        bases = {
            resolver.ROOT_DNS_IP: {"br": {"type": "NS", "value": "172.20.0.101"}},
            "172.20.0.101": {"ufjf.br": {"type": "NS", "value": "172.20.0.102"}},
            "172.20.0.102": {"www.ufjf.br": {"type": "A", "value": "172.20.0.40"}},
        }
        orcamentos = []
        sockets = []
        with patch("prazos.time.monotonic", return_value=100) as relogio:
            def criar_socket(*argumentos):
                socket_teste = Mock()
                sockets.append(socket_teste)

                def enviar(dados, endereco):
                    consulta = json.loads(dados)
                    orcamentos.append(consulta["tempo_restante"])
                    prazo = criar_prazo(consulta)
                    relogio.return_value += 1  # Espera na fila do próximo servidor.
                    resposta = dns_server.resolver_consulta(consulta, bases[endereco[0]], prazo)
                    socket_teste.recvfrom.return_value = json.dumps(resposta).encode(), endereco

                socket_teste.sendto.side_effect = enviar
                return socket_teste

            with patch("socket.socket", side_effect=criar_socket):
                resultado = resolver.resolver_consulta(self.consulta(rd=True))
        self.assertEqual(orcamentos, [5, 4, 3])
        self.assertEqual(resultado["status"], "SUCCESS")
        self.assertEqual(resultado["transaction_id"], 1)
        for socket_teste in sockets:
            socket_teste.close.assert_called_once()

    def test_resposta_recebida_apos_prazo_nao_entra_no_cache(self):
        for recursiva in (False, True):
            with self.subTest(recursiva=recursiva):
                socket_teste = Mock()
                with patch("prazos.time.monotonic", return_value=100) as relogio:
                    def receber(tamanho):
                        relogio.return_value = 106
                        return json.dumps(self.resposta()).encode(), (resolver.ROOT_DNS_IP, 5300)

                    socket_teste.recvfrom.side_effect = receber
                    with patch("socket.socket", return_value=socket_teste), \
                            patch("resolver.secrets.randbits", return_value=1):
                        resultado = resolver.resolver_consulta(self.consulta(rd=recursiva))
                self.assertIsNone(resultado)
                self.assertEqual(resolver.cache, {})
                socket_teste.close.assert_called_once()

    def test_consulta_vence_na_fila_e_trabalhador_atende_a_proxima(self):
        iniciou = Event()
        liberar = Event()
        socket_teste = Mock()
        chamadas = iter(range(4))
        processadas = []
        with patch("prazos.time.monotonic", return_value=100) as relogio:
            def receber(tamanho):
                indice = next(chamadas)
                if indice == 1:
                    self.assertTrue(iniciou.wait(2))
                if indice == 2:
                    relogio.return_value = 106
                    liberar.set()
                if indice == 3:
                    raise KeyboardInterrupt
                return json.dumps(self.consulta(indice + 1)).encode(), ("127.0.0.1", 40000)

            def processar(consulta, prazo=None):
                processadas.append(consulta["transaction_id"])
                if consulta["transaction_id"] == 1:
                    iniciou.set()
                    if not liberar.wait(2):
                        raise RuntimeError("O teste não liberou o trabalhador")
                return self.resposta(consulta["transaction_id"])

            socket_teste.recvfrom.side_effect = receber
            with self.assertRaises(KeyboardInterrupt):
                atender_consultas(socket_teste, processar, "TESTE", trabalhadores=1,
                                  limite_por_ip=20, limite_global=100)
        self.assertEqual(processadas, [1, 3])
        socket_teste.sendto.assert_called_once()
        self.assertEqual(json.loads(socket_teste.sendto.call_args.args[0])["transaction_id"], 3)

    def test_consulta_ja_vencida_nao_acessa_cache_nem_rede(self):
        with patch("prazos.time.monotonic", return_value=105), \
                patch("resolver.check_cache") as buscar_cache, \
                patch("socket.socket") as criar_socket:
            self.assertIsNone(resolver.resolver_consulta(self.consulta(), prazo=105))
            self.assertIsNone(dns_server.resolver_consulta(self.consulta(), {}, prazo=105))
        buscar_cache.assert_not_called()
        criar_socket.assert_not_called()

    def test_cliente_espera_seis_segundos_e_informa_orcamento_de_cinco(self):
        socket_teste = Mock()
        socket_teste.recvfrom.return_value = (json.dumps(self.resposta()).encode(),
                                             (client_test.RESOLVER_IP, client_test.RESOLVER_PORT))
        with patch("prazos.time.monotonic", return_value=100), \
                patch("socket.socket", return_value=socket_teste), \
                patch("client_test.secrets.randbits", return_value=1):
            client_test.enviar_consulta("www.ufjf.br")
        consulta = json.loads(socket_teste.sendto.call_args.args[0])
        self.assertEqual(consulta["tempo_restante"], 5)
        for chamada in socket_teste.settimeout.call_args_list:
            self.assertEqual(chamada.args[0], 6)
        socket_teste.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
