import socket
import json
import os
import sys

# Porta padrão de escuta dos servidores DNS internos
PORT = 5300

def load_db(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar banco de dados local: {e}")
        return {}

def main():
    # Carrega o papel e banco de dados via variáveis de ambiente
    role = os.getenv("DNS_ROLE", "ROOT") # ROOT, TLD ou AUTH
    db_path = os.getenv("DB_PATH", "records.json")
    db = load_db(db_path)

    # Configura o Socket UDP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))

    print(f"Servidor Mini-DNS iniciado como papel [{role}] escutando na porta {PORT}...")
    while True:
        data, addr = sock.recvfrom(4096)
        try:
            query = json.loads(data.decode('utf-8'))
            print(f"[{role}] Recebeu consulta de {addr}: {query}")
            tx_id = query.get("transaction_id")
            qname = query.get("qname")
            qtype = query.get("qtype")
            recursion_desired = query.get("rd", False)

            response = {
            "transaction_id": tx_id,
            "status": "NXDOMAIN",
            "answer": [],
            "authority": []
            }
            # TODO: 1. Verificar se o domínio exato ou sufixo correspondente está no DB local
            # TODO: 2. Se estiver e for o servidor Autorizativo final, preencher a "answer" e retornar "SUCCESS
            # TODO: 3. Se for ROOT ou TLD e a requisição for ITERATIVA (rd = False), preencher "authority" com
            # TODO: 4. Se a requisição for RECURSIVA (rd = True) e o nó intermediário precisar buscar recursiva
        
            # Envia a resposta JSON de volta ao solicitante
            sock.sendto(json.dumps(response).encode('utf-8'), addr)

        except Exception as e:
            print(f"Erro ao processar pacote de {addr}: {e}")
    
if __name__ == "__main__":
    main()