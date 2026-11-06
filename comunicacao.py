import json
import socket
import time
from validacao import resposta_valida


def aguardar_resposta(sock, query, servidor, tempo_limite):
    limite = time.monotonic() + tempo_limite

    while True:
        restante = limite - time.monotonic()
        if restante <= 0:
            raise socket.timeout("Tempo limite da consulta esgotado")
        sock.settimeout(restante)
        dados, origem = sock.recvfrom(4096)

        if origem != servidor:
            print(f"[CONSULTA] Resposta de {origem} descartada: servidor diferente de {servidor}.")
            continue
        try:
            resposta = json.loads(dados.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
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
        return resposta
