import socket
import json
import time
import os

# Configurações do Resolver Local
RESOLVER_PORT = 5353
ROOT_DNS_IP = os.getenv("ROOT_DNS_IP", "172.20.0.100") # closure mechanism
SERVER_PORT = 5300

# Cache local em memória
# Estrutura esperada: { "dominio": { "ip": "...", "expires_at": 1786812345, "type": "A" } }
cache = {}

def check_cache(domain):
    now = time.time()
    if domain in cache:
        record = cache[domain]
        # TODO: Verificar se o registro ainda está válido comparando now com expires_at
        if now < record["expires_at"]:
            print(f"[CACHE HIT] {domain} -> {record['ip']}")
            return record["ip"]
        else:
            print(f"[CACHE EXPIRED] Registro expirado para {domain}")
            del cache[domain]
    return None

def save_to_cache(domain, ip, ttl):
    # TODO: Implementar lógica de salvar no cache calculando o expires_at (time.time() + ttl)
    expires_at = time.time() + ttl
    cache[domain] = {
        "ip": ip,
        "expires_at": expires_at,
        "ttl": ttl
    }
    print(f"[CACHE SAVE] Registrado {domain} -> {ip} por {ttl} segundos.")

def resolve_iterative(domain):
    # Algoritmo de Resolução Iterativa clássico
    current_dns = ROOT_DNS_IP
    tx_id = 1001

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0) # Timeout de segurança de rede

    print(f"[RESOLVER] Iniciando busca iterativa para '{domain}' a partir do Root: {current_dns}")

    while True:
        # TODO: 1. Montar a mensagem Query JSON com rd=False (iterativa)
        query = {
            "transaction_id": tx_id,
            "qname": domain,
            "qtype": "A",
            "rd": False
        }
        try:
            # TODO: 2. Enviar via UDP para current_dns
            sock.sendto(json.dumps(query).encode('utf-8'), (current_dns, SERVER_PORT))
            # TODO: 3. Aguardar a resposta
            data, _ = sock.recvfrom(4096)
            response = json.loads(data.decode('utf-8'))
            status = response.get("status")
            # TODO: 4. Analisar o status retornado:
            # - Se "SUCCESS": Retornar o IP final e armazenar no Cache local com seu respectivo TTL.
            if status == "SUCCESS":
                if response.get("answer"):
                    ans = response["answer"][0]
                    ip = ans["value"]
                    ttl = ans.get("ttl", 60)
                    save_to_cache(domain, ip, ttl)
                    sock.close()
                    return ip
            # - Se "REFERRAL": Ler o IP do próximo servidor indicado no campo "authority" e atualizar curr
            elif status == "REFERRAL":
                if response.get("authority"):
                    auth = response["authority"][0]
                    current_dns = auth["value"]
                    print(f"[RESOLVER] Indicado para o próximo servidor (Referral): {current_dns}")
                else:
                    break
            # - Se "NXDOMAIN": Encerrar a busca, domínio inexistente.
            elif status == "NXDOMAIN":
                print(f"[RESOLVER] Domínio {domain} inexistente (NXDOMAIN).")
                break
        except socket.timeout:
            print(f"[RESOLVER] Timeout ao consultar o servidor {current_dns}")
            break
        except Exception as e:
            print(f"[RESOLVER] Erro durante a resolução: {e}")
            break

    sock.close()
    return None

def main():
    # Inicializa o socket para ouvir requisições dos clientes (ex: browser ou CLI)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', RESOLVER_PORT))

    print(f"Local Resolver ativo na porta {RESOLVER_PORT}...")

    while True:
        data, addr = sock.recvfrom(4096)
        query = json.loads(data.decode('utf-8'))
        domain = query.get("qname")

        # 1. Tenta buscar do cache local primeiro
        cached_ip = check_cache(domain)
        if cached_ip:
            response = {"status": "SUCCESS", "answer": [{"name": domain, "value": cached_ip}]}
            sock.sendto(json.dumps(response).encode('utf-8'), addr)
            continue

        # 2. Se falhar, dispara a resolução iterativa de rede
        ip_resolvido = resolve_iterative(domain)
        if ip_resolvido:
            response = {"status": "SUCCESS", "answer": [{"name": domain, "value": ip_resolvido}]}
        else:
            response = {"status": "NXDOMAIN", "answer": []}

        sock.sendto(json.dumps(response).encode('utf-8'), addr)

if __name__ == "__main__":
    main()