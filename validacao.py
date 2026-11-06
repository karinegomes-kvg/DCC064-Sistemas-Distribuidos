from ipaddress import AddressValueError, IPv4Address
import math


def id_valido(valor):
    return type(valor) is int and 0 <= valor <= 65535


def consulta_valida(consulta):
    if not isinstance(consulta, dict) or not id_valido(consulta.get("transaction_id")):
        return False
    nome = consulta.get("qname")
    if not isinstance(nome, str) or not nome.strip():
        return False
    if type(consulta.get("rd")) is not bool:
        return False
    if "tempo_restante" in consulta:
        tempo = consulta["tempo_restante"]
        if type(tempo) not in (int, float) or tempo <= 0:
            return False
        if type(tempo) is float and not math.isfinite(tempo):
            return False
    return True


def registro_valido(registro):
    if not isinstance(registro, dict):
        return False
    if registro.get("type") not in ("A", "NS"):
        return False

    # Neste Mini-DNS, A e NS guardam um IPv4 no campo "value".
    valor = registro.get("value")
    if not isinstance(valor, str):
        return False
    try:
        endereco = IPv4Address(valor)
    except AddressValueError:
        return False
    if str(endereco) != valor:
        return False

    if "ttl" in registro:
        ttl = registro["ttl"]
        if type(ttl) is not int or ttl < 0:
            return False

    # Na base local, o nome fica na chave do dicionário.
    if "name" in registro:
        nome = registro["name"]
        if not isinstance(nome, str) or not nome.strip():
            return False
    return True


def resposta_valida(resposta):
    if not isinstance(resposta, dict):
        return False
    if not id_valido(resposta.get("transaction_id")):
        return False
    status = resposta.get("status")
    if status not in ("SUCCESS", "REFERRAL", "NXDOMAIN"):
        return False

    registros = resposta.get("answer", [])
    servidores = resposta.get("authority", [])
    if not isinstance(registros, list) or not isinstance(servidores, list):
        return False

    for registro in registros:
        if not registro_valido(registro) or not registro.get("name"):
            return False
    for servidor in servidores:
        if not registro_valido(servidor) or not servidor.get("name"):
            return False
        if servidor["type"] != "NS":
            return False

    if status == "SUCCESS" and not registros:
        return False
    if status == "REFERRAL" and not servidores:
        return False
    return True
