import json
import socket
import runpy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import dns_server
import resolver
import client_test


class RedeSimulada:
    """Entrega os datagramas aos servidores usando as bases reais do projeto."""

    def __init__(self):
        projeto = Path(__file__).resolve().parents[1]
        base_raiz = json.loads((projeto / "root_db.json").read_text())
        base_tld = json.loads((projeto / "tld_db.json").read_text())
        base_autoritativo = json.loads((projeto / "auth_db.json").read_text())
        self.raiz = resolver.ROOT_DNS_IP
        self.tld = base_raiz["br"]["value"]
        self.autoritativo = base_tld["ufjf.br"]["value"]
        self.bases = {self.raiz: base_raiz, self.tld: base_tld, self.autoritativo: base_autoritativo}
        self.enviadas = []

    def criar_socket(self, *argumentos):
        return SocketSimulado(self)


class SocketSimulado:
    def __init__(self, rede):
        self.rede = rede

    def settimeout(self, tempo_limite):
        pass

    def sendto(self, dados, endereco):
        consulta = json.loads(dados.decode("utf-8"))
        self.rede.enviadas.append((endereco[0], consulta))
        self.endereco = endereco
        if endereco == (client_test.RESOLVER_IP, client_test.RESOLVER_PORT):
            self.resposta = resolver.resolver_consulta(consulta)
        else:
            self.resposta = dns_server.resolver_consulta(consulta, self.rede.bases[endereco[0]])

    def recvfrom(self, tamanho):
        return json.dumps(self.resposta).encode("utf-8"), self.endereco

    def close(self):
        pass


class TestesTiposConsulta(unittest.TestCase):
    def setUp(self):
        resolver.cache.clear()
        self.addCleanup(resolver.cache.clear)
        self.rede = RedeSimulada()
        substituicao_socket = patch("socket.socket", side_effect=self.rede.criar_socket)
        substituicao_socket.start()
        self.addCleanup(substituicao_socket.stop)
        substituicao_print = patch("builtins.print")
        substituicao_print.start()
        self.addCleanup(substituicao_print.stop)

    def consulta(self, dominio, qtype, rd=False):
        return {"transaction_id": 45182, "qname": dominio, "qtype": qtype, "rd": rd}

    def conferir_resposta(self, resposta, dominio, qtype, valor):
        self.assertEqual(resposta["status"], "SUCCESS")
        self.assertEqual(resposta["transaction_id"], 45182)
        self.assertEqual(resposta["authority"], [])
        self.assertEqual(len(resposta["answer"]), 1)
        registro = resposta["answer"][0]
        self.assertEqual((registro["name"], registro["type"], registro["value"]),
                         (dominio, qtype, valor))
        self.assertGreaterEqual(registro["ttl"], 0)

    def test_consulta_a_percorre_todos_os_servidores(self):
        resposta = resolver.resolver_consulta(self.consulta("www.ufjf.br", "A"))
        valor = self.rede.bases[self.rede.autoritativo]["www.ufjf.br"]["value"]
        self.conferir_resposta(resposta, "www.ufjf.br", "A", valor)
        self.assertEqual([ip for ip, _ in self.rede.enviadas],
                         [self.rede.raiz, self.rede.tld, self.rede.autoritativo])

    def test_novos_registros_google_com_sao_resolvidos(self):
        dominios = (
            "mail.google.com",
            "drive.google.com",
            "maps.google.com",
            "docs.google.com",
            "photos.google.com",
        )
        for dominio in dominios:
            with self.subTest(dominio=dominio):
                self.rede.enviadas.clear()
                resposta = resolver.resolver_consulta(self.consulta(dominio, "A"))
                valor = self.rede.bases[self.rede.autoritativo][dominio]["value"]
                self.conferir_resposta(resposta, dominio, "A", valor)
                self.assertEqual(
                    [ip for ip, _ in self.rede.enviadas],
                    [self.rede.raiz, self.rede.tld, self.rede.autoritativo],
                )

    def test_cada_etapa_gera_id_e_resposta_mantem_id_do_cliente(self):
        with patch("resolver.secrets.randbits", side_effect=[0, 32768, 65535]) as gerar_id:
            resposta = resolver.resolver_consulta(self.consulta("www.ufjf.br", "A"))
        self.assertEqual([consulta["transaction_id"] for _, consulta in self.rede.enviadas],
                         [0, 32768, 65535])
        self.assertEqual(gerar_id.call_count, 3)
        for chamada in gerar_id.call_args_list:
            self.assertEqual(chamada.args, (16,))
        self.assertEqual(resposta["status"], "SUCCESS")
        self.assertEqual(resposta["transaction_id"], 45182)

    def test_consulta_ns_para_no_servidor_com_o_registro(self):
        for dominio, valor, servidores in (
            ("br", self.rede.tld, [self.rede.raiz]),
            ("ufjf.br", self.rede.autoritativo, [self.rede.raiz, self.rede.tld]),
        ):
            with self.subTest(dominio=dominio):
                self.rede.enviadas.clear()
                resposta = resolver.resolver_consulta(self.consulta(dominio, "NS"))
                self.conferir_resposta(resposta, dominio, "NS", valor)
                self.assertEqual([ip for ip, _ in self.rede.enviadas], servidores)
                self.assertTrue(all(consulta["qtype"] == "NS" for _, consulta in self.rede.enviadas))

    def test_encaminhamento_para_consultas_a_e_ns(self):
        db = self.rede.bases[self.rede.raiz]
        for qtype in ("A", "NS"):
            with self.subTest(qtype=qtype):
                resposta = dns_server.resolver_consulta(self.consulta("www.ufjf.br", qtype), db)
                self.assertEqual(resposta["status"], "REFERRAL")
                self.assertEqual(resposta["answer"], [])
                self.assertEqual(resposta["authority"][0]["name"], "br")

    def test_consulta_recursiva_mantem_o_tipo_solicitado(self):
        for dominio, qtype, valor in (
            ("ufjf.br", "NS", self.rede.autoritativo),
            ("www.ufjf.br", "A",
             self.rede.bases[self.rede.autoritativo]["www.ufjf.br"]["value"]),
        ):
            with self.subTest(qtype=qtype):
                self.rede.enviadas.clear()
                resposta = dns_server.resolver_consulta(
                    self.consulta(dominio, qtype, rd=True),
                    self.rede.bases[self.rede.raiz],
                )
                self.conferir_resposta(resposta, dominio, qtype, valor)
                self.assertTrue(self.rede.enviadas)
                self.assertTrue(all(consulta["qtype"] == qtype and consulta["rd"]
                                    for _, consulta in self.rede.enviadas))

    def test_resolver_respeita_rd_e_percorre_cadeia_recursiva(self):
        valor_a = self.rede.bases[self.rede.autoritativo]["www.ufjf.br"]["value"]
        for dominio, tipo, valor, servidores in (
            ("www.ufjf.br", "A", valor_a,
             [self.rede.raiz, self.rede.tld, self.rede.autoritativo]),
            ("ufjf.br", "NS", self.rede.autoritativo, [self.rede.raiz, self.rede.tld]),
            ("br", "NS", self.rede.tld, [self.rede.raiz]),
        ):
            with self.subTest(dominio=dominio, tipo=tipo):
                self.rede.enviadas.clear()
                resposta = resolver.resolver_consulta(self.consulta(dominio, tipo, rd=True))
                self.conferir_resposta(resposta, dominio, tipo, valor)
                self.assertEqual([ip for ip, _ in self.rede.enviadas], servidores)
                self.assertTrue(all(consulta["rd"] for _, consulta in self.rede.enviadas))
                self.assertEqual(resolver.cache[(dominio, tipo)]["registro"]["value"], valor)

    def test_opcao_recursiva_no_cliente_percorre_todo_o_fluxo(self):
        for dominio, tipo, servidores in (
            ("www.ufjf.br", "A", [self.rede.raiz, self.rede.tld, self.rede.autoritativo]),
            ("ufjf.br", "NS", [self.rede.raiz, self.rede.tld]),
        ):
            with self.subTest(dominio=dominio, tipo=tipo):
                resolver.cache.clear()
                self.rede.enviadas.clear()
                argumentos = ["client_test.py", dominio, "--qtype", tipo, "--recursiva"]
                with patch("sys.argv", argumentos):
                    runpy.run_module("client_test", run_name="__main__")
                self.assertEqual([ip for ip, _ in self.rede.enviadas],
                                 [client_test.RESOLVER_IP] + servidores)
                self.assertTrue(all(consulta["rd"] for _, consulta in self.rede.enviadas))
                self.assertIn((dominio, tipo), resolver.cache)

    def test_cliente_com_dominio_vazio_nao_inicia_bateria_de_testes(self):
        with patch("sys.argv", ["client_test.py", ""]), patch("time.sleep") as esperar:
            runpy.run_module("client_test", run_name="__main__")
        self.assertEqual(self.rede.enviadas, [])
        esperar.assert_not_called()

    def test_cache_e_compartilhado_entre_os_modos(self):
        for primeiro_modo in (False, True):
            with self.subTest(primeiro_modo=primeiro_modo):
                resolver.cache.clear()
                primeira = resolver.resolver_consulta(
                    self.consulta("www.ufjf.br", "A", rd=primeiro_modo)
                )
                self.rede.enviadas.clear()
                consulta = self.consulta("www.ufjf.br", "A", rd=not primeiro_modo)
                consulta["transaction_id"] = 123
                resposta = resolver.resolver_consulta(consulta)
                self.assertEqual(resposta["status"], "SUCCESS")
                self.assertEqual(resposta["transaction_id"], 123)
                self.assertEqual(resposta["answer"][0]["value"], primeira["answer"][0]["value"])
                self.assertEqual(self.rede.enviadas, [])

    def test_consulta_recursiva_inexistente_retorna_nxdomain(self):
        for dominio, tipo, servidores in (
            ("google.com", "A", [self.rede.raiz, self.rede.tld, self.rede.autoritativo]),
            ("www.ufjf.br", "NS", [self.rede.raiz, self.rede.tld, self.rede.autoritativo]),
        ):
            with self.subTest(dominio=dominio, tipo=tipo):
                self.rede.enviadas.clear()
                resposta = resolver.resolver_consulta(self.consulta(dominio, tipo, rd=True))
                self.assertEqual(resposta["status"], "NXDOMAIN")
                self.assertEqual(resposta["transaction_id"], 45182)
                self.assertEqual([ip for ip, _ in self.rede.enviadas], servidores)
                self.assertEqual(resolver.cache, {})

    def test_recursao_gera_ids_e_restaura_id_da_consulta_original(self):
        with patch("dns_server.secrets.randbits", side_effect=[25, 65535]) as gerar_id:
            resposta = dns_server.resolver_consulta(
                self.consulta("www.ufjf.br", "A", rd=True),
                self.rede.bases[self.rede.raiz],
            )
        self.assertEqual([consulta["transaction_id"] for _, consulta in self.rede.enviadas],
                         [25, 65535])
        self.assertEqual(gerar_id.call_count, 2)
        self.assertEqual(resposta["status"], "SUCCESS")
        self.assertEqual(resposta["transaction_id"], 45182)

    def test_consultas_com_id_invalido_sao_descartadas(self):
        for identificador in (None, True, False, -1, 65536, 1.5, "1234"):
            with self.subTest(identificador=identificador):
                consulta = self.consulta("www.ufjf.br", "A")
                consulta["transaction_id"] = identificador
                self.assertIsNone(resolver.resolver_consulta(consulta))
                self.assertIsNone(dns_server.resolver_consulta(
                    consulta, self.rede.bases[self.rede.raiz]
                ))
        self.assertEqual(self.rede.enviadas, [])
        self.assertEqual(resolver.cache, {})

    def test_consulta_ns_nao_usa_registro_a_do_cache(self):
        resolver.resolver_consulta(self.consulta("www.ufjf.br", "A"))
        self.rede.enviadas.clear()
        resposta = resolver.resolver_consulta(self.consulta("www.ufjf.br", "NS"))
        self.assertEqual(resposta["status"], "NXDOMAIN")
        self.assertEqual(resposta["answer"], [])
        self.assertEqual(len(self.rede.enviadas), 3)

    def test_consulta_a_nao_usa_registro_ns_do_cache(self):
        resolver.resolver_consulta(self.consulta("ufjf.br", "NS"))
        self.rede.enviadas.clear()
        resposta = resolver.resolver_consulta(self.consulta("ufjf.br", "A"))
        self.assertEqual(resposta["status"], "NXDOMAIN")
        self.assertEqual(resposta["answer"], [])
        self.assertEqual(len(self.rede.enviadas), 3)

    def test_cache_mantem_tipo_ttl_e_id_da_consulta(self):
        for dominio, qtype in (("www.ufjf.br", "A"), ("ufjf.br", "NS")):
            with self.subTest(qtype=qtype):
                with patch("resolver.time.time", return_value=100):
                    primeira = resolver.resolver_consulta(self.consulta(dominio, qtype))
                self.rede.enviadas.clear()
                consulta = self.consulta(dominio, qtype)
                consulta["transaction_id"] = 123
                with patch("resolver.time.time", return_value=104):
                    cacheada = resolver.resolver_consulta(consulta)
                self.assertEqual(self.rede.enviadas, [])
                self.assertEqual(cacheada["transaction_id"], 123)
                self.assertEqual(cacheada["answer"][0]["type"], qtype)
                self.assertEqual(cacheada["answer"][0]["value"], primeira["answer"][0]["value"])
                self.assertEqual(cacheada["answer"][0]["ttl"], primeira["answer"][0]["ttl"] - 4)

    def test_registros_expirados_sao_consultados_novamente(self):
        for dominio, qtype in (("www.ufjf.br", "A"), ("ufjf.br", "NS")):
            with self.subTest(qtype=qtype):
                with patch("resolver.time.time", return_value=100):
                    primeira = resolver.resolver_consulta(self.consulta(dominio, qtype))
                self.rede.enviadas.clear()
                expiracao = 100 + primeira["answer"][0]["ttl"]
                with patch("resolver.time.time", return_value=expiracao):
                    resposta = resolver.resolver_consulta(self.consulta(dominio, qtype))
                self.assertEqual(resposta["status"], "SUCCESS")
                self.assertTrue(self.rede.enviadas)

    def test_registro_a_nao_responde_por_outros_subdominios(self):
        resposta = resolver.resolver_consulta(self.consulta("inexistente.www.ufjf.br", "A"))
        self.assertEqual(resposta["status"], "NXDOMAIN")
        self.assertEqual(resposta["answer"], [])

    def test_resolver_recusa_resposta_com_tipo_incorreto(self):
        resposta_incorreta = {
            "transaction_id": 1234,
            "status": "SUCCESS",
            "answer": [{"name": "ufjf.br", "type": "A", "value": "192.0.2.1", "ttl": 30}],
        }
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            (json.dumps(resposta_incorreta).encode(), (self.rede.raiz, 5300)),
            socket.timeout,
        ]
        with patch("socket.socket", return_value=socket_teste), \
                patch("resolver.secrets.randbits", return_value=1234):
            resposta = resolver.resolver_consulta(self.consulta("ufjf.br", "NS"))
        self.assertIsNone(resposta)
        self.assertEqual(resolver.cache, {})
        self.assertEqual(socket_teste.sendto.call_count, 1)

    def test_tipo_invalido_nao_consulta_rede_nem_grava_cache(self):
        consulta = self.consulta("www.ufjf.br", "MX")
        resposta = resolver.resolver_consulta(consulta)
        self.assertEqual(resposta["status"], "NXDOMAIN")
        self.assertEqual(self.rede.enviadas, [])
        resposta = dns_server.resolver_consulta(consulta, self.rede.bases[self.rede.autoritativo])
        self.assertEqual(resposta["status"], "NXDOMAIN")
        self.assertEqual(resposta["answer"], [])


if __name__ == "__main__":
    unittest.main()
