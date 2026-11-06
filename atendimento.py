import json
from queue import Full, Queue
from threading import Thread
from validacao import consulta_valida


NUM_TRABALHADORES = 4
LIMITE_FILA = 8


def atender_consultas(sock, processar_consulta, nome_servidor,
                      trabalhadores=NUM_TRABALHADORES, limite_fila=LIMITE_FILA):
    if type(trabalhadores) is not int or trabalhadores <= 0:
        raise ValueError("A quantidade de trabalhadores precisa ser um inteiro positivo")
    if type(limite_fila) is not int or limite_fila <= 0:
        raise ValueError("O limite da fila precisa ser um inteiro positivo")
    fila = Queue(maxsize=limite_fila)
    equipe = []

    def processar_fila():
        while True:
            item = fila.get()
            try:
                if item is None:
                    return
                query, addr = item
                try:
                    response = processar_consulta(query)
                    if response is not None:
                        sock.sendto(json.dumps(response).encode('utf-8'), addr)
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
            # Um pacote inválido não deve ocupar a fila nem interromper o atendimento.
            try:
                query = json.loads(data.decode('utf-8'))
            except (UnicodeDecodeError, ValueError, RecursionError) as e:
                print(f"[{nome_servidor}] Pacote inválido de {addr}, descartado: {e}")
                continue
            if not consulta_valida(query):
                print(f"[{nome_servidor}] Consulta de {addr} descartada: ID, domínio ou rd inválido.")
                continue

            try:
                fila.put_nowait((query, addr))
            except Full:
                print(f"[{nome_servidor}] Fila cheia, consulta de {addr} descartada.")
                continue
            print(f"[{nome_servidor}] Recebeu consulta de {addr}: {query}")
    finally:
        # Finaliza os trabalhadores depois das consultas já aceitas.
        for trabalhador in equipe:
            fila.put(None)
        for trabalhador in equipe:
            trabalhador.join()
