import json
import time
from validacao import resposta_valida
from prazos import TEMPO_TOTAL, tempo_restante


def aguardar_resposta(sock, query, servidor, tempo_limite=None, prazo=None):
    if prazo is None:
        if tempo_limite is None:
            tempo_limite = TEMPO_TOTAL
        prazo = time.monotonic() + tempo_limite
    elif tempo_limite is not None:
        prazo = min(prazo, time.monotonic() + tempo_limite)

    while True:
        sock.settimeout(tempo_restante(prazo))
        dados, origem = sock.recvfrom(4096)
        tempo_restante(prazo)

        if origem != servidor:
            print(f"[CONSULTA] Resposta de {origem} descartada: servidor diferente de {servidor}.")
            continue
        try:
            resposta = json.loads(dados.decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            print(f"[CONSULTA] Resposta inválida de {servidor} descartada.")
            continue

        if not resposta_valida(resposta):
            print(f"[CONSULTA] Resposta inválida de {servidor} descartada.")
            continue
        if resposta["transaction_id"] != query["transaction_id"]:
            print(f"[CONSULTA] Resposta de {servidor} descartada: ID de outra consulta.")
            continue

        corresponde = True
        if resposta["status"] == "SUCCESS":
            for registro in resposta["answer"]:
                if registro["name"] != query["qname"] or registro["type"] != query["qtype"]:
                    corresponde = False
                    break
        elif resposta["status"] == "REFERRAL":
            for registro in resposta["authority"]:
                zona = registro["name"]
                if query["qname"] != zona and not query["qname"].endswith("." + zona):
                    corresponde = False
                    break
        if not corresponde:
            print(f"[CONSULTA] Resposta de {servidor} descartada: nome ou tipo diferente da consulta.")
            continue
        tempo_restante(prazo)
        return resposta
