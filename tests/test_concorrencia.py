import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock, patch

import dns_server
import resolver
from atendimento import atender_consultas
from validacao import registro_valido


class TestesConcorrencia(unittest.TestCase):
    def setUp(self):
        resolver.cache.clear()
        self.addCleanup(resolver.cache.clear)
        substituicao_print = patch("builtins.print")
        substituicao_print.start()
        self.addCleanup(substituicao_print.stop)

    def consulta(self, identificador, dominio="teste.br", rd=False):
        return {"transaction_id": identificador, "qname": dominio, "qtype": "A", "rd": rd}

    def registro(self, dominio, valor="192.0.2.1", tipo="A", ttl=60):
        return {"name": dominio, "type": tipo, "value": valor, "ttl": ttl}

    def pacote(self, consulta):
        return json.dumps(consulta).encode("utf-8"), ("127.0.0.1", 40000 + consulta["transaction_id"])

    def resposta(self, consulta):
        return {"transaction_id": consulta["transaction_id"], "status": "NXDOMAIN",
                "answer": [], "authority": []}

    def conferir_consultas_simultaneas(self, iniciar, destino_espera, resultado_lento, rd=False):
        lenta_iniciou = Event()
        liberar_lenta = Event()
        rapida_respondeu = Event()
        lenta_respondeu = Event()
        lenta = self.consulta(1, "lenta.br", rd)
        rapida = self.consulta(2, "rapida.br", rd)
        socket_teste = Mock()
        recebimentos = iter((lenta, rapida, None))
        enviadas = []

        def esperar(*argumentos, **opcoes):
            lenta_iniciou.set()
            if not liberar_lenta.wait(2):
                raise TimeoutError("O teste não liberou a consulta lenta")
            return resultado_lento

        def receber(tamanho):
            consulta = next(recebimentos)
            if consulta is lenta:
                return self.pacote(lenta)
            if consulta is rapida:
                self.assertTrue(lenta_iniciou.wait(2))
                return self.pacote(rapida)
            try:
                self.assertTrue(rapida_respondeu.wait(2))
                self.assertFalse(lenta_respondeu.is_set())
            finally:
                liberar_lenta.set()
            raise KeyboardInterrupt

        def enviar(dados, endereco):
            resposta = json.loads(dados.decode("utf-8"))
            enviadas.append((resposta, endereco))
            if resposta["transaction_id"] == 2:
                rapida_respondeu.set()
            else:
                lenta_respondeu.set()

        socket_teste.recvfrom.side_effect = receber
        socket_teste.sendto.side_effect = enviar
        with patch("socket.socket", return_value=socket_teste), \
                patch(destino_espera, side_effect=esperar):
            with self.assertRaises(KeyboardInterrupt):
                iniciar()
        self.assertEqual([resposta["transaction_id"] for resposta, _ in enviadas], [2, 1])
        for resposta, endereco in enviadas:
            self.assertEqual(resposta["status"], "SUCCESS")
            self.assertEqual(endereco, ("127.0.0.1", 40000 + resposta["transaction_id"]))
        socket_teste.close.assert_called_once()

    def test_resolver_responde_cache_enquanto_outra_consulta_espera(self):
        resolver.save_to_cache("rapida.br", self.registro("rapida.br"))
        self.conferir_consultas_simultaneas(
            resolver.main, "resolver.resolve_iterative", self.registro("lenta.br")
        )

    def test_servidor_responde_enquanto_outra_consulta_faz_recursao(self):
        base = {
            "br": self.registro("br", "192.0.2.3", "NS", 300),
            "rapida.br": self.registro("rapida.br"),
        }
        resposta_lenta = {"transaction_id": 999, "status": "SUCCESS",
                          "answer": [self.registro("lenta.br")], "authority": []}
        with patch("dns_server.load_db", return_value=base):
            self.conferir_consultas_simultaneas(
                dns_server.main, "dns_server.query_upstream", resposta_lenta, rd=True
            )

    def test_fila_fifo_descarta_excedentes_e_nao_guarda_consultas_invalidas(self):
        iniciou = Event()
        liberar = Event()
        socket_teste = Mock()
        chamadas = iter(range(5))
        processadas = []

        def processar(consulta):
            processadas.append(consulta["transaction_id"])
            if consulta["transaction_id"] == 1:
                iniciou.set()
                if not liberar.wait(2):
                    raise TimeoutError("O teste não liberou o trabalhador")
            return self.resposta(consulta)

        def receber(tamanho):
            indice = next(chamadas)
            if indice == 0:
                return self.pacote(self.consulta(1))
            if indice == 1:
                self.assertTrue(iniciou.wait(2))
                return b"{}", ("127.0.0.1", 40000)
            if indice == 2:
                return self.pacote(self.consulta(2))
            if indice == 3:
                return self.pacote(self.consulta(3))
            liberar.set()
            raise KeyboardInterrupt

        socket_teste.recvfrom.side_effect = receber
        with self.assertRaises(KeyboardInterrupt):
            atender_consultas(socket_teste, processar, "TESTE", trabalhadores=1, limite_fila=1)
        self.assertEqual(processadas, [1, 2])
        self.assertEqual(socket_teste.recvfrom.call_count, 5)
        self.assertEqual(socket_teste.sendto.call_count, 2)
        ids = []
        for chamada in socket_teste.sendto.call_args_list:
            ids.append(json.loads(chamada.args[0].decode("utf-8"))["transaction_id"])
        self.assertEqual(ids, [1, 2])

    def test_erro_em_uma_consulta_nao_encerra_o_trabalhador(self):
        socket_teste = Mock()
        socket_teste.recvfrom.side_effect = [
            self.pacote(self.consulta(1)), self.pacote(self.consulta(2)), KeyboardInterrupt
        ]
        processar = Mock(side_effect=[ValueError("Falha na consulta"), self.resposta(self.consulta(2))])
        with self.assertRaises(KeyboardInterrupt):
            atender_consultas(socket_teste, processar, "TESTE", trabalhadores=1, limite_fila=2)
        self.assertEqual(processar.call_count, 2)
        socket_teste.sendto.assert_called_once()
        resposta = json.loads(socket_teste.sendto.call_args.args[0].decode("utf-8"))
        self.assertEqual(resposta["transaction_id"], 2)

    def test_cache_suporta_gravacoes_leituras_e_expiracoes_simultaneas(self):
        def usar_cache(indice):
            tipo = "A"
            if indice % 2:
                tipo = "NS"
            for tentativa in range(100):
                ttl = 60
                if tentativa % 3 == 0:
                    ttl = 0
                registro = self.registro("teste.br", tipo=tipo, ttl=ttl)
                resolver.save_to_cache("teste.br", registro)
                encontrado = resolver.check_cache("teste.br", tipo)
                if encontrado is not None:
                    self.assertTrue(registro_valido(encontrado))
                    self.assertEqual(encontrado["type"], tipo)
                    self.assertEqual(encontrado["name"], "teste.br")

        with ThreadPoolExecutor(max_workers=4) as equipe:
            tarefas = [equipe.submit(usar_cache, indice) for indice in range(4)]
            for tarefa in tarefas:
                tarefa.result(timeout=2)

    def test_limites_invalidos_nao_criam_filas_sem_limite(self):
        for argumentos in ({"trabalhadores": 0}, {"trabalhadores": -1},
                           {"limite_fila": 0}, {"limite_fila": -1}):
            with self.subTest(argumentos=argumentos):
                with self.assertRaises(ValueError):
                    atender_consultas(Mock(), Mock(), "TESTE", **argumentos)


if __name__ == "__main__":
    unittest.main()
