import json
import socket
import unittest
from unittest.mock import Mock, call, patch

import client_test
import dns_server
import resolver
from comunicacao import aguardar_resposta


class TestesTransacoes(unittest.TestCase):
    def setUp(self):
        resolver.cache.clear()
        self.addCleanup(resolver.cache.clear)
        substituicao_print = patch("builtins.print")
        substituicao_print.start()
        self.addCleanup(substituicao_print.stop)
        self.servidor = (resolver.ROOT_DNS_IP, resolver.SERVER_PORT)
        self.consulta = {
            "transaction_id": 1, "qname": "www.ufjf.br", "qtype": "A", "rd": False
        }

    def resposta(self, identificador=1, nome="www.ufjf.br", tipo="A", valor="172.20.0.40"):
        return {
            "transaction_id": identificador,
            "status": "SUCCESS",
            "answer": [{"name": nome, "type": tipo, "value": valor, "ttl": 15}],
            "authority": [],
        }

    def pacote(self, resposta, origem=None):
        if origem is None:
            origem = self.servidor
        return json.dumps(resposta).encode("utf-8"), origem

    def test_descarta_pacotes_incorretos_e_aguarda_resposta_valida(self):
        pacotes = [
            self.pacote(self.resposta(identificador=2)),
            self.pacote(self.resposta(), ("192.0.2.99", self.servidor[1])),
            self.pacote(self.resposta(), (self.servidor[0], self.servidor[1] + 1)),
            self.pacote(self.resposta(nome="outro.br")),
            self.pacote(self.resposta(tipo="NS")),
            self.pacote({"status": "NXDOMAIN"}),
            self.pacote([]),
            (b"{", self.servidor),
            (b"\xff", self.servidor),
            self.pacote({
                "transaction_id": 1, "status": "REFERRAL", "authority": [
                    {"name": "outro.com", "type": "NS", "value": "192.0.2.1", "ttl": 60}
                ]
            }),
        ]
        for identificador in (True, False, 1.0, "1", -1, 65536, None):
            pacotes.append(self.pacote(self.resposta(identificador=identificador)))

        correta = self.resposta()
        for pacote in pacotes:
            with self.subTest(pacote=pacote):
                socket_teste = Mock()
                socket_teste.recvfrom.side_effect = [pacote, self.pacote(correta)]
                resultado = aguardar_resposta(socket_teste, self.consulta, self.servidor, 3.0)
                self.assertEqual(resultado, correta)
                self.assertEqual(socket_teste.recvfrom.call_count, 2)

    def test_descartes_nao_reiniciam_timeout(self):
        socket_teste = Mock()
        socket_teste.recvfrom.return_value = self.pacote(self.resposta(identificador=2))
        with patch("comunicacao.time.monotonic", side_effect=[100, 100, 101, 102.5, 103]):
            with self.assertRaises(socket.timeout):
                aguardar_resposta(socket_teste, self.consulta, self.servidor, 3.0)
        self.assertEqual(socket_teste.settimeout.call_args_list,
                         [call(3.0), call(2.0), call(0.5)])
        self.assertEqual(socket_teste.recvfrom.call_count, 3)

    def test_resolver_so_armazena_resposta_da_consulta_e_servidor_corretos(self):
        socket_teste = Mock()
        correta = self.resposta()
        socket_teste.recvfrom.side_effect = [
            self.pacote(self.resposta(identificador=2, valor="192.0.2.99")),
            self.pacote(self.resposta(valor="192.0.2.99"), ("192.0.2.99", 5300)),
            self.pacote(correta),
        ]
        with patch("socket.socket", return_value=socket_teste), \
                patch("resolver.secrets.randbits", return_value=1):
            resultado = resolver.resolver_consulta(self.consulta)
        self.assertEqual(resultado["answer"], correta["answer"])
        self.assertEqual(resolver.cache[("www.ufjf.br", "A")]["registro"]["value"],
                         "172.20.0.40")
        self.assertEqual(socket_teste.sendto.call_count, 1)
        self.assertEqual(socket_teste.recvfrom.call_count, 3)
        socket_teste.close.assert_called_once()

    def test_timeout_apos_id_incorreto_nao_grava_cache(self):
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            self.pacote(self.resposta(identificador=2)), socket.timeout
        ]
        with patch("socket.socket", return_value=socket_teste), \
                patch("resolver.secrets.randbits", return_value=1):
            resultado = resolver.resolver_consulta(self.consulta)
        self.assertEqual(resultado["status"], "NXDOMAIN")
        self.assertEqual(resolver.cache, {})
        self.assertEqual(socket_teste.sendto.call_count, 1)
        socket_teste.close.assert_called_once()

    def test_cliente_gera_id_de_16_bits_a_cada_consulta_e_confere_resposta(self):
        sockets = []
        for identificador in (0, 65535):
            socket_teste = Mock()
            origem = (client_test.RESOLVER_IP, client_test.RESOLVER_PORT)
            socket_teste.recvfrom.side_effect = [
                self.pacote(self.resposta(identificador=(identificador + 1) % 65536), origem),
                self.pacote(self.resposta(identificador=identificador), origem),
            ]
            sockets.append(socket_teste)
        with patch("socket.socket", side_effect=sockets), \
                patch("client_test.secrets.randbits", side_effect=[0, 65535]) as gerar_id:
            client_test.enviar_consulta("www.ufjf.br")
            client_test.enviar_consulta("www.ufjf.br")
        self.assertEqual(gerar_id.call_args_list, [call(16), call(16)])
        for socket_teste, identificador in zip(sockets, (0, 65535)):
            consulta = json.loads(socket_teste.sendto.call_args.args[0].decode("utf-8"))
            self.assertEqual(consulta["transaction_id"], identificador)
            self.assertEqual(socket_teste.sendto.call_args.args[1], origem)
            self.assertEqual(socket_teste.recvfrom.call_count, 2)
            socket_teste.close.assert_called_once()

    def test_encaminhamento_recursivo_descarta_id_incorreto(self):
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            self.pacote(self.resposta(identificador=54320)),
            self.pacote(self.resposta(identificador=54321)),
        ]
        with patch("socket.socket", return_value=socket_teste), \
                patch("dns_server.secrets.randbits", return_value=54321) as gerar_id:
            resultado = dns_server.query_upstream(
                self.servidor[0], "www.ufjf.br", "A", True
            )
        consulta = json.loads(socket_teste.sendto.call_args.args[0].decode("utf-8"))
        gerar_id.assert_called_once_with(16)
        self.assertEqual(consulta["transaction_id"], 54321)
        self.assertTrue(consulta["rd"])
        self.assertEqual(resultado["transaction_id"], 54321)
        self.assertEqual(socket_teste.recvfrom.call_count, 2)
        socket_teste.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
