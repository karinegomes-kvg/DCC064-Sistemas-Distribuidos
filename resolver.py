import socket
import json
import time
import os
import secrets
from threading import Lock
from atendimento import atender_consultas
from comunicacao import aguardar_resposta
from prazos import criar_prazo, tempo_restante
from validacao import consulta_valida, registro_valido

# Configurações do Resolver Local
RESOLVER_PORT = 5353
ROOT_DNS_IP = os.getenv("ROOT_DNS_IP", "172.20.0.100") # closure mechanism
SERVER_PORT = 5300

# Cache local em memória
# Estrutura: { ("dominio", "tipo"): { "registro": {...}, "expires_at": 1786812345 } }
cache = {}
trava_cache = Lock()

def check_cache(domain, qtype="A"):
    chave = (domain, qtype)
    with trava_cache:
        now = time.time()
        if chave not in cache:
            return None
        registro_cache = cache[chave]
        # TODO: Verificar se o registro ainda está válido comparando now com expires_at
        if now < registro_cache["expires_at"]:
            registro = registro_cache["registro"].copy()
            registro["ttl"] = int(registro_cache["expires_at"] - now)
        else:
            del cache[chave]
            registro = None
    if registro is None:
        print(f"[CACHE EXPIRED] Registro expirado para {domain} ({qtype})")
    else:
        print(f"[CACHE HIT] {domain} ({qtype}) -> {registro['value']}")
    return registro

def save_to_cache(domain, registro):
    # TODO: Implementar lógica de salvar no cache calculando o expires_at (time.time() + ttl)
    if not registro_valido(registro) or "ttl" not in registro:
        print(f"[CACHE] Registro inválido para {domain}, não foi armazenado.")
        return
    ttl = registro["ttl"]
    with trava_cache:
        expires_at = time.time() + ttl
        cache[(domain, registro["type"])] = {
            "registro": registro.copy(),
            "expires_at": expires_at
        }
    print(f"[CACHE SAVE] Registrado {domain} ({registro['type']}) -> {registro['value']} por {ttl} segundos.")

def resolve_iterative(domain, qtype="A", prazo=None):
    # Algoritmo de Resolução Iterativa clássico
    current_dns = ROOT_DNS_IP
    if prazo is None:
        prazo = criar_prazo()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"[RESOLVER] Iniciando busca iterativa para '{domain}' ({qtype}) a partir do Root: {current_dns}")

    while True:
        # TODO: 1. Montar a mensagem Query JSON com rd=False (iterativa)
        tx_id = secrets.randbits(16)
        query = {
            "transaction_id": tx_id,
            "qname": domain,
            "qtype": qtype,
            "rd": False
        }
        try:
            # TODO: 2. Enviar via UDP para current_dns
            restante = tempo_restante(prazo)
            query["tempo_restante"] = restante
            sock.settimeout(restante)
            sock.sendto(json.dumps(query).encode('utf-8'), (current_dns, SERVER_PORT))
            # TODO: 3. Aguardar a resposta
            response = aguardar_resposta(sock, query, (current_dns, SERVER_PORT), prazo=prazo)
            status = response.get("status")
            # - Se "SUCCESS": Retornar o registro e armazenar no Cache local com seu respectivo TTL.
            if status == "SUCCESS":
                registro = None
                for resposta in response.get("answer", []):
                    if resposta.get("name") == domain and resposta.get("type") == qtype:
                        registro = resposta.copy()
                        break
                if registro:
                    if "ttl" not in registro:
                        if qtype == "NS":
                            registro["ttl"] = 300
                        else:
                            registro["ttl"] = 60
                    tempo_restante(prazo)
                    save_to_cache(domain, registro)
                    sock.close()
                    return registro
                break
            # - Se "REFERRAL": Ler o IP indicado em "authority" e atualizar current_dns.
            elif status == "REFERRAL":
                servidores = response.get("authority")
                if not servidores:
                    break
                current_dns = servidores[0]["value"]
                print(f"[RESOLVER] Indicado para o próximo servidor (Referral): {current_dns}")
            # - Se "NXDOMAIN": Encerrar a busca, domínio inexistente.
            elif status == "NXDOMAIN":
                print(f"[RESOLVER] Domínio {domain} inexistente (NXDOMAIN).")
                break
            else:
                break
        except socket.timeout:
            sock.close()
            raise
        except Exception as e:
            print(f"[RESOLVER] Erro durante a resolução: {e}")
            break

    sock.close()
    return None

def resolver_recursivo(domain, qtype="A", prazo=None):
    # O Root fica responsável por consultar os próximos servidores.
    if prazo is None:
        prazo = criar_prazo()
    query = {
        "transaction_id": secrets.randbits(16),
        "qname": domain,
        "qtype": qtype,
        "rd": True
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"[RESOLVER] Iniciando busca recursiva para '{domain}' ({qtype}) pelo Root: {ROOT_DNS_IP}")

    try:
        restante = tempo_restante(prazo)
        query["tempo_restante"] = restante
        sock.settimeout(restante)
        sock.sendto(json.dumps(query).encode('utf-8'), (ROOT_DNS_IP, SERVER_PORT))
        response = aguardar_resposta(sock, query, (ROOT_DNS_IP, SERVER_PORT), prazo=prazo)
        if response["status"] == "SUCCESS":
            registro = response["answer"][0].copy()
            if "ttl" not in registro:
                if qtype == "NS":
                    registro["ttl"] = 300
                else:
                    registro["ttl"] = 60
            tempo_restante(prazo)
            save_to_cache(domain, registro)
            return registro
        elif response["status"] == "NXDOMAIN":
            print(f"[RESOLVER] Domínio {domain} inexistente (NXDOMAIN).")
        else:
            print("[RESOLVER] O Root retornou encaminhamento em uma consulta recursiva.")
    except socket.timeout:
        raise
    except Exception as e:
        print(f"[RESOLVER] Erro durante a resolução: {e}")
    finally:
        sock.close()
    return None

def resolver_consulta(query, prazo=None):
    if not consulta_valida(query):
        print("[RESOLVER] Consulta descartada: ID, domínio, rd ou tempo restante inválido.")
        return None
    if prazo is None:
        prazo = criar_prazo(query)
    try:
        tempo_restante(prazo)
    except socket.timeout:
        return None
    domain = query.get("qname")
    qtype = query.get("qtype", "A")
    response = {
        "transaction_id": query.get("transaction_id"),
        "status": "NXDOMAIN",
        "answer": [],
        "authority": []
    }
    if qtype not in ("A", "NS"):
        return response

    # 1. Tenta buscar do cache local primeiro
    registro = check_cache(domain, qtype)
    if registro is None:
        # 2. Se falhar, faz a busca de rede no modo solicitado
        try:
            if query["rd"]:
                registro = resolver_recursivo(domain, qtype, prazo=prazo)
            else:
                registro = resolve_iterative(domain, qtype, prazo=prazo)
        except socket.timeout:
            print(f"[RESOLVER] Prazo total esgotado para '{domain}' ({qtype}).")
            return None
    if registro is not None:
        response["status"] = "SUCCESS"
        response["answer"].append(registro)
    return response

def main():
    # Inicializa o socket para ouvir requisições dos clientes (ex: browser ou CLI)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', RESOLVER_PORT))

    print(f"Local Resolver ativo na porta {RESOLVER_PORT}...")

    try:
        atender_consultas(sock, resolver_consulta, "RESOLVER")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
