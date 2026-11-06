import json
import socket
import unittest
from unittest.mock import Mock, patch

import dns_server
import resolver
import client_test
from validacao import consulta_valida, registro_valido, resposta_valida


class TestesValidacao(unittest.TestCase):
    def setUp(self):
        resolver.cache.clear()
        self.addCleanup(resolver.cache.clear)
        substituicao_print = patch("builtins.print")
        substituicao_print.start()
        self.addCleanup(substituicao_print.stop)

    def consulta(self, tipo="A", rd=False):
        return {"transaction_id": 1234, "qname": "teste.br", "qtype": tipo, "rd": rd}

    def registro(self, tipo="A"):
        return {"name": "teste.br", "type": tipo, "value": "192.0.2.1", "ttl": 60}

    def consultar_resposta(self, resposta, tipo="A"):
        socket_teste = Mock()

        def enviar(dados, endereco):
            consulta = json.loads(dados.decode("utf-8"))
            resposta_enviada = resposta.copy()
            resposta_enviada["transaction_id"] = consulta["transaction_id"]
            socket_teste.recvfrom.side_effect = [
                (json.dumps(resposta_enviada).encode("utf-8"), endereco),
                socket.timeout,
            ]

        socket_teste.sendto.side_effect = enviar
        with patch("socket.socket", return_value=socket_teste):
            resultado = resolver.resolver_consulta(self.consulta(tipo))
        return resultado, socket_teste

    def test_rd_precisa_ser_booleano(self):
        with patch("socket.socket") as criar_socket, patch("resolver.check_cache") as buscar_cache:
            for rd in (None, 0, 1, 0.0, 1.0, "false", "true", [], {}):
                with self.subTest(rd=rd):
                    consulta = self.consulta(rd=rd)
                    self.assertFalse(consulta_valida(consulta))
                    self.assertIsNone(resolver.resolver_consulta(consulta))
                    self.assertIsNone(dns_server.resolver_consulta(
                        consulta, {"teste.br": self.registro()}
                    ))
            criar_socket.assert_not_called()
            buscar_cache.assert_not_called()

    def test_qname_precisa_ser_texto_nao_vazio(self):
        with patch("socket.socket") as criar_socket, patch("resolver.check_cache") as buscar_cache:
            for nome in ("", " ", "\t\n", None, 1234, True, [], {}):
                with self.subTest(nome=nome):
                    consulta = self.consulta()
                    consulta["qname"] = nome
                    self.assertFalse(consulta_valida(consulta))
                    self.assertIsNone(resolver.resolver_consulta(consulta))
                    self.assertIsNone(dns_server.resolver_consulta(
                        consulta, {"teste.br": self.registro()}
                    ))
            criar_socket.assert_not_called()
            buscar_cache.assert_not_called()

    def test_consulta_sem_rd_ou_qname_e_descartada(self):
        for campo in ("rd", "qname"):
            with self.subTest(campo=campo):
                consulta = self.consulta()
                del consulta[campo]
                self.assertIsNone(resolver.resolver_consulta(consulta))
                self.assertIsNone(dns_server.resolver_consulta(consulta, {}))

    def test_cliente_valida_dominio_e_rd_antes_de_abrir_socket(self):
        with patch("socket.socket") as criar_socket:
            for dominio, rd in (("", False), (" ", True), (None, False),
                                ("teste.br", "false"), ("teste.br", 1)):
                with self.subTest(dominio=dominio, rd=rd):
                    client_test.enviar_consulta(dominio, recursiva=rd)
            criar_socket.assert_not_called()

    def test_resolver_continua_atendendo_apos_pacotes_invalidos(self):
        resolver.save_to_cache("teste.br", self.registro())
        socket_teste = Mock()
        origem = ("127.0.0.1", 40000)
        outra_origem = ("127.0.0.1", 40001)
        invalidos = (
            b"", b"{", b'{"transaction_id": 1234', b"\xff",
            b"[" * 1500 + b"]" * 1500,
            b"[]", b"null", b'"texto"', b"123", b"{}",
            json.dumps(self.consulta(rd="false")).encode("utf-8"),
        )
        primeira = self.consulta()
        segunda = self.consulta()
        segunda["transaction_id"] = 5678
        pacotes = [(dados, origem) for dados in invalidos]
        pacotes.extend([
            (json.dumps(primeira).encode("utf-8"), origem),
            (b"\xff", outra_origem),
            (json.dumps(segunda).encode("utf-8"), outra_origem),
            KeyboardInterrupt,
        ])
        socket_teste.recvfrom.side_effect = pacotes
        with patch("socket.socket", return_value=socket_teste):
            with self.assertRaises(KeyboardInterrupt):
                resolver.main()

        self.assertEqual(socket_teste.sendto.call_count, 2)
        enderecos = {primeira["transaction_id"]: origem, segunda["transaction_id"]: outra_origem}
        respondidos = set()
        for chamada in socket_teste.sendto.call_args_list:
            dados, destino = chamada.args
            resposta = json.loads(dados.decode("utf-8"))
            identificador = resposta["transaction_id"]
            self.assertIn(identificador, enderecos)
            self.assertEqual(destino, enderecos[identificador])
            self.assertEqual(resposta["status"], "SUCCESS")
            self.assertEqual(resposta["answer"][0]["value"], "192.0.2.1")
            respondidos.add(identificador)
        self.assertEqual(respondidos, set(enderecos))

    def test_resolver_continua_atendendo_apos_erro_no_envio(self):
        resolver.save_to_cache("teste.br", self.registro())
        socket_teste = Mock()
        origem = ("127.0.0.1", 40000)
        dados = json.dumps(self.consulta()).encode("utf-8")
        socket_teste.recvfrom.side_effect = [(dados, origem), (dados, origem), KeyboardInterrupt]
        socket_teste.sendto.side_effect = [OSError("Falha no envio UDP"), None]
        with patch("socket.socket", return_value=socket_teste):
            with self.assertRaises(KeyboardInterrupt):
                resolver.main()
        self.assertEqual(socket_teste.sendto.call_count, 2)
        resposta = json.loads(socket_teste.sendto.call_args.args[0].decode("utf-8"))
        self.assertEqual(resposta["status"], "SUCCESS")
        self.assertEqual(resposta["transaction_id"], 1234)

    def test_aceita_ipv4_em_texto_para_a_e_ns(self):
        for tipo in ("A", "NS"):
            for valor in ("0.0.0.0", "172.20.0.40", "255.255.255.255"):
                with self.subTest(tipo=tipo, valor=valor):
                    registro = self.registro(tipo)
                    registro["value"] = valor
                    self.assertTrue(registro_valido(registro))

    def test_servidor_recusa_valores_incompativeis_com_o_tipo(self):
        valores = (
            "::1", "2001:db8::1", "256.20.0.40", "172.20.0", "172.020.0.40",
            "172.20.0.40/24", "servidor.br", "", None, 3232235521, True, [], {}
        )
        for tipo in ("A", "NS"):
            for valor in valores:
                with self.subTest(tipo=tipo, valor=valor):
                    registro = self.registro(tipo)
                    registro["value"] = valor
                    resposta = dns_server.resolver_consulta(
                        self.consulta(tipo), {"teste.br": registro}
                    )
                    self.assertEqual(resposta["status"], "NXDOMAIN")
                    self.assertEqual(resposta["answer"], [])
                    self.assertEqual(resposta["authority"], [])

    def test_servidor_nao_encaminha_para_ns_invalido(self):
        registro = self.registro("NS")
        registro["value"] = "servidor.br"
        for rd in (False, True):
            with self.subTest(rd=rd):
                with patch("dns_server.query_upstream") as consulta_externa:
                    resposta = dns_server.resolver_consulta(
                        self.consulta(rd=rd), {"br": registro}
                    )
                self.assertEqual(resposta["status"], "NXDOMAIN")
                self.assertEqual(resposta["authority"], [])
                consulta_externa.assert_not_called()

    def test_resolver_recusa_valores_invalidos_sem_gravar_cache(self):
        for tipo in ("A", "NS"):
            for valor in ("::1", "999.0.0.1", "servidor.br", 1234, None):
                with self.subTest(tipo=tipo, valor=valor):
                    registro = self.registro(tipo)
                    registro["value"] = valor
                    resposta, socket_teste = self.consultar_resposta(
                        {"status": "SUCCESS", "answer": [registro]}, tipo
                    )
                    self.assertEqual(resposta["status"], "NXDOMAIN")
                    self.assertEqual(resolver.cache, {})
                    self.assertEqual(socket_teste.sendto.call_count, 1)
                    socket_teste.close.assert_called_once()

    def test_resolver_recusa_encaminhamento_invalido(self):
        servidores = []
        registro = self.registro("NS")
        registro["value"] = "::1"
        servidores.append(registro)
        servidores.append(self.registro("A"))
        registro = self.registro("NS")
        del registro["name"]
        servidores.append(registro)
        for servidor in servidores:
            with self.subTest(servidor=servidor):
                resposta, socket_teste = self.consultar_resposta(
                    {"status": "REFERRAL", "authority": [servidor]}
                )
                self.assertEqual(resposta["status"], "NXDOMAIN")
                self.assertEqual(socket_teste.sendto.call_count, 1)
                self.assertEqual(resolver.cache, {})

    def test_ttl_invalido_nao_e_respondido_nem_armazenado(self):
        for ttl in (-1, 1.5, "60", True, False, None):
            with self.subTest(ttl=ttl):
                registro = self.registro()
                registro["ttl"] = ttl
                resposta = dns_server.resolver_consulta(
                    self.consulta(), {"teste.br": registro}
                )
                self.assertEqual(resposta["status"], "NXDOMAIN")
                resposta, _ = self.consultar_resposta(
                    {"status": "SUCCESS", "answer": [registro]}
                )
                self.assertEqual(resposta["status"], "NXDOMAIN")
                resolver.save_to_cache("teste.br", registro)
                self.assertEqual(resolver.cache, {})

    def test_ttl_ausente_usa_valor_padrao_do_tipo(self):
        for tipo, ttl in (("A", 60), ("NS", 300)):
            with self.subTest(tipo=tipo):
                registro = self.registro(tipo)
                del registro["ttl"]
                resposta = dns_server.resolver_consulta(
                    self.consulta(tipo), {"teste.br": registro}
                )
                self.assertEqual(resposta["answer"][0]["ttl"], ttl)
                resposta, _ = self.consultar_resposta(
                    {"status": "SUCCESS", "answer": [registro]}, tipo
                )
                self.assertEqual(resposta["status"], "SUCCESS")
                self.assertEqual(resposta["answer"][0]["ttl"], ttl)
                self.assertIn(("teste.br", tipo), resolver.cache)

    def test_ttl_zero_e_valido_e_expira_imediatamente(self):
        registro = self.registro()
        registro["ttl"] = 0
        self.assertTrue(registro_valido(registro))
        with patch("resolver.time.time", return_value=100):
            resolver.save_to_cache("teste.br", registro)
            self.assertIsNone(resolver.check_cache("teste.br"))
        self.assertEqual(resolver.cache, {})

    def test_recursao_recusa_valor_nome_ou_tipo_incorreto(self):
        registros = []
        registro = self.registro()
        registro["value"] = "::1"
        registros.append(registro)
        registros.append(self.registro("NS"))
        registro = self.registro()
        registro["name"] = "outro.br"
        registros.append(registro)
        for registro in registros:
            with self.subTest(registro=registro):
                socket_teste = Mock()
                resposta = {"transaction_id": 1234, "status": "SUCCESS", "answer": [registro]}
                socket_teste.recvfrom.side_effect = [
                    (json.dumps(resposta).encode("utf-8"), ("192.0.2.1", 5300)),
                    socket.timeout,
                ]
                with patch("socket.socket", return_value=socket_teste), \
                        patch("dns_server.secrets.randbits", return_value=1234):
                    resultado = dns_server.resolver_consulta(
                        self.consulta(rd=True), {"br": self.registro("NS")}
                    )
                self.assertEqual(resultado["status"], "NXDOMAIN")
                socket_teste.close.assert_called_once()

    def test_resposta_exige_listas_e_registros_com_nome(self):
        respostas = (
            [], None, {"status": "SUCCESS", "answer": []},
            {"status": "SUCCESS", "answer": self.registro()},
            {"status": "SUCCESS", "answer": [None]},
            {"status": "REFERRAL", "authority": {}},
            {"status": "REFERRAL", "authority": []},
            {"status": "SUCCESS", "answer": [{"type": "A", "value": "192.0.2.1"}]},
        )
        for resposta in respostas:
            with self.subTest(resposta=resposta):
                if isinstance(resposta, dict):
                    resposta["transaction_id"] = 1234
                self.assertFalse(resposta_valida(resposta))


if __name__ == "__main__":
    unittest.main()
