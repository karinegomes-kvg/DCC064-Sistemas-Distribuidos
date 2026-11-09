import socket
import time


TEMPO_TOTAL = 5.0
MARGEM_CLIENTE = 1.0
TEMPO_CLIENTE = TEMPO_TOTAL + MARGEM_CLIENTE


def criar_prazo(consulta=None, recebido_em=None):
    if recebido_em is None:
        recebido_em = time.monotonic()
    duracao = TEMPO_TOTAL
    if consulta is not None:
        duracao = min(consulta.get("tempo_restante", TEMPO_TOTAL), TEMPO_TOTAL)
    return recebido_em + duracao


def tempo_restante(prazo):
    restante = prazo - time.monotonic()
    if restante <= 0:
        raise socket.timeout("Prazo total da consulta esgotado")
    return restante
