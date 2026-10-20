import socket
import json
import time
import os

# Configurações do Resolver Local
RESOLVER_PORT = 5353
ROOT_DNS_IP = os.getenv("ROOT_DNS_IP", "172.20.0.100") # closure mechanism

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
    pass

def resolve_iterative(domain):
    # Algoritmo de Resolução Iterativa clássico
    current_dns = ROOT_DNS_IP
    tx_id = 1001

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0) # Timeout de segurança de rede

    print(f"[RESOLVER] Iniciando busca iterativa para '{domain}' a partir do Root: {current_dns}")

    while True:
        # TODO: 1. Montar a mensagem Query JSON com rd=False (iterativa)
        # TODO: 2. Enviar via UDP para current_dns
        # TODO: 3. Aguardar a resposta
        # TODO: 4. Analisar o status retornado:
        # - Se "SUCCESS": Retornar o IP final e armazenar no Cache local com seu respectivo TTL.
        # - Se "REFERRAL": Ler o IP do próximo servidor indicado no campo "authority" e atualizar curr
        # - Se "NXDOMAIN": Encerrar a busca, domínio inexistente.

        # Simulação temporária de quebra de loop infinito para prevenção do esqueleto
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