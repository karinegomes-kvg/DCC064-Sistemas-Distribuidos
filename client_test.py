import socket
import json
import time

# O Local Resolver está exposto na porta 5354 do host
RESOLVER_IP = "127.0.0.1"
RESOLVER_PORT = 5354

def enviar_consulta(dominio):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    
    query = {
        "transaction_id": 1234,
        "qname": dominio,
        "qtype": "A",
        "rd": False  # Consulta iterativa
    }
    
    print(f"\n---> Enviando consulta para '{dominio}'...")
    try:
        sock.sendto(json.dumps(query).encode('utf-8'), (RESOLVER_IP, RESOLVER_PORT))
        data, _ = sock.recvfrom(4096)
        resposta = json.loads(data.decode('utf-8'))
        print(f"<--- Resposta recebida:\n{json.dumps(resposta, indent=2)}")
    except socket.timeout:
        print("<--- Timeout: Sem resposta do Local Resolver.")
    except Exception as e:
        print(f"<--- Erro ao enviar pacote: {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    print("=== BATERIA DE TESTES - MINI-DNS DISTRIBUÍDO ===")

    # Teste 1: Primeira busca (deve fazer a resolução iterativa completa)
    print("\n[TESTE 1] Primeira consulta para 'www.ufjf.br' (Busca na Rede)")
    enviar_consulta("www.ufjf.br")

    # Teste 2: Segunda busca imediata (deve dar Cache Hit)
    print("\n[TESTE 2] Segunda consulta imediata para 'www.ufjf.br' (Cache Hit)")
    enviar_consulta("www.ufjf.br")

    # Teste 3: Domínio inexistente (deve retornar NXDOMAIN)
    print("\n[TESTE 3] Consulta por domínio inexistente 'google.com'")
    enviar_consulta("google.com")

    # Teste 4: Expiração do TTL
    tempo_espera = 16
    print(f"\n[TESTE 4] Aguardando {tempo_espera} segundos para o TTL (15s) expirar...")
    time.sleep(tempo_espera)

    print("\n[TESTE 4 - Continuação] Consulta por 'www.ufjf.br' pós-TTL (Invalidação e Nova Busca)")
    enviar_consulta("www.ufjf.br")

    print("\n=== BATERIA DE TESTES CONCLUÍDA ===")

# 1. Primeira Consulta: www.ufjf.br (Busca na Rede)
# Cliente: Recebe status: "SUCCESS" e IP "172.20.0.40".

# 2. Segunda Consulta: www.ufjf.br (Cache Hit)
# Cliente: Recebe status: "SUCCESS" e IP "172.20.0.40" de imediato.

# 3. Consulta por Domínio Inexistente: google.com
# Cliente: Recebe status: "NXDOMAIN".

#Consulta Pós-TTL: www.ufjf.br (Após 16 segundos)
# Cliente: Recebe status: "SUCCESS" e IP "172.20.0.40".