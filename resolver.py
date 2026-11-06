import socket
import json
import time
import os
from validacao import registro_valido, resposta_valida

# Configurações do Resolver Local
RESOLVER_PORT = 5353
ROOT_DNS_IP = os.getenv("ROOT_DNS_IP", "172.20.0.100") # closure mechanism
SERVER_PORT = 5300

# Cache local em memória
# Estrutura: { ("dominio", "tipo"): { "registro": {...}, "expires_at": 1786812345 } }
cache = {}

def check_cache(domain, qtype="A"):
    now = time.time()
    chave = (domain, qtype)
    if chave in cache:
        registro_cache = cache[chave]
        # TODO: Verificar se o registro ainda está válido comparando now com expires_at
        if now < registro_cache["expires_at"]:
            registro = registro_cache["registro"].copy()
            registro["ttl"] = int(registro_cache["expires_at"] - now)
            print(f"[CACHE HIT] {domain} ({qtype}) -> {registro['value']}")
            return registro
        else:
            print(f"[CACHE EXPIRED] Registro expirado para {domain} ({qtype})")
            del cache[chave]
    return None

def save_to_cache(domain, registro):
    # TODO: Implementar lógica de salvar no cache calculando o expires_at (time.time() + ttl)
    if not registro_valido(registro) or "ttl" not in registro:
        print(f"[CACHE] Registro inválido para {domain}, não foi armazenado.")
        return
    ttl = registro["ttl"]
    expires_at = time.time() + ttl
    cache[(domain, registro["type"])] = {
        "registro": registro.copy(),
        "expires_at": expires_at
    }
    print(f"[CACHE SAVE] Registrado {domain} ({registro['type']}) -> {registro['value']} por {ttl} segundos.")

def resolve_iterative(domain, qtype="A"):
    # Algoritmo de Resolução Iterativa clássico
    current_dns = ROOT_DNS_IP
    tx_id = 1001

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0) # Timeout de segurança de rede

    print(f"[RESOLVER] Iniciando busca iterativa para '{domain}' ({qtype}) a partir do Root: {current_dns}")

    while True:
        # TODO: 1. Montar a mensagem Query JSON com rd=False (iterativa)
        query = {
            "transaction_id": tx_id,
            "qname": domain,
            "qtype": qtype,
            "rd": False
        }
        try:
            # TODO: 2. Enviar via UDP para current_dns
            sock.sendto(json.dumps(query).encode('utf-8'), (current_dns, SERVER_PORT))
            # TODO: 3. Aguardar a resposta
            data, _ = sock.recvfrom(4096)
            response = json.loads(data.decode('utf-8'))
            if not resposta_valida(response):
                print(f"[RESOLVER] Resposta inválida do servidor {current_dns}")
                break
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
            print(f"[RESOLVER] Timeout ao consultar o servidor {current_dns}")
            break
        except Exception as e:
            print(f"[RESOLVER] Erro durante a resolução: {e}")
            break

    sock.close()
    return None

def resolver_consulta(query):
    domain = query.get("qname")
    qtype = query.get("qtype", "A")
    response = {
        "transaction_id": query.get("transaction_id"),
        "status": "NXDOMAIN",
        "answer": [],
        "authority": []
    }
    if not isinstance(domain, str) or qtype not in ("A", "NS"):
        return response

    # 1. Tenta buscar do cache local primeiro
    registro = check_cache(domain, qtype)
    if registro is None:
        # 2. Se falhar, dispara a resolução iterativa de rede
        registro = resolve_iterative(domain, qtype)
    if registro is not None:
        response["status"] = "SUCCESS"
        response["answer"].append(registro)
    return response

def main():
    # Inicializa o socket para ouvir requisições dos clientes (ex: browser ou CLI)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', RESOLVER_PORT))

    print(f"Local Resolver ativo na porta {RESOLVER_PORT}...")

    while True:
        data, addr = sock.recvfrom(4096)
        query = json.loads(data.decode('utf-8'))
        response = resolver_consulta(query)
        sock.sendto(json.dumps(response).encode('utf-8'), addr)

if __name__ == "__main__":
    main()
