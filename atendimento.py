import json
import os
import socket
import time
from queue import Full, Queue
from threading import Thread
from validacao import consulta_valida
from controle_carga import ControleCarga
from prazos import criar_prazo, tempo_restante


NUM_TRABALHADORES = 4
LIMITE_FILA = 8
LIMITE_POR_IP = 20
LIMITE_GLOBAL = 100


def atender_consultas(sock, processar_consulta, nome_servidor,
                      trabalhadores=NUM_TRABALHADORES, limite_fila=LIMITE_FILA,
                      limite_por_ip=None, limite_global=None):
    if type(trabalhadores) is not int or trabalhadores <= 0:
        raise ValueError("A quantidade de trabalhadores precisa ser um inteiro positivo")
    if type(limite_fila) is not int or limite_fila <= 0:
        raise ValueError("O limite da fila precisa ser um inteiro positivo")
    if limite_por_ip is None:
        limite_por_ip = int(os.getenv("LIMITE_POR_IP", str(LIMITE_POR_IP)))
    if limite_global is None:
        limite_global = int(os.getenv("LIMITE_GLOBAL", str(LIMITE_GLOBAL)))
    controle = ControleCarga(limite_por_ip, limite_global)
    ultimo_aviso = None
    fila = Queue(maxsize=limite_fila)
    equipe = []

    def processar_fila():
        while True:
            item = fila.get()
            try:
                if item is None:
                    return
                query, addr, prazo = item
                try:
                    tempo_restante(prazo)
                    response = processar_consulta(query, prazo=prazo)
                    if response is not None:
                        dados = json.dumps(response).encode('utf-8')
                        tempo_restante(prazo)
                        sock.sendto(dados, addr)
                except socket.timeout:
                    print(f"[{nome_servidor}] Prazo total esgotado para a consulta "
                          f"{query['transaction_id']} de {addr}, descartada.")
                except Exception as e:
                    print(f"[{nome_servidor}] Erro ao processar consulta de {addr}: {e}")
            finally:
                fila.task_done()

    try:
        for indice in range(trabalhadores):
            trabalhador = Thread(target=processar_fila,
                                 name=f"{nome_servidor}-{indice + 1}", daemon=True)
            trabalhador.start()
            equipe.append(trabalhador)

        while True:
            data, addr = sock.recvfrom(4096)
            recebido_em = time.monotonic()
            # Limita todos os pacotes, inclusive os inválidos, antes de interpretar o JSON.
            if not controle.aceitar(addr[0]):
                agora = time.monotonic()
                if ultimo_aviso is None or agora - ultimo_aviso >= 1.0:
                    print(f"[{nome_servidor}] Limite de carga: pacote de {addr} descartado; "
                          f"totais por IP={controle.descartados_por_ip}, "
                          f"globais={controle.descartados_globais}.")
                    ultimo_aviso = agora
                continue
            # Um pacote inválido não deve ocupar a fila nem interromper o atendimento.
            try:
                query = json.loads(data.decode('utf-8'))
            except (UnicodeDecodeError, ValueError, RecursionError) as e:
                print(f"[{nome_servidor}] Pacote inválido de {addr}, descartado: {e}")
                continue
            if not consulta_valida(query):
                print(f"[{nome_servidor}] Consulta de {addr} descartada: ID, domínio, rd ou tempo restante inválido.")
                continue

            try:
                prazo = criar_prazo(query, recebido_em)
                fila.put_nowait((query, addr, prazo))
            except Full:
                print(f"[{nome_servidor}] Fila cheia, consulta de {addr} descartada.")
                continue
            print(f"[{nome_servidor}] Recebeu consulta de {addr}: {query}")
    finally:
        if controle.descartados_por_ip or controle.descartados_globais:
            print(f"[{nome_servidor}] Total de descartes por limite: "
                  f"por IP={controle.descartados_por_ip}, globais={controle.descartados_globais}.")
        # Finaliza os trabalhadores depois das consultas já aceitas.
        for trabalhador in equipe:
            fila.put(None)
        for trabalhador in equipe:
            trabalhador.join()
