import socket
import json
import os
import secrets
from comunicacao import aguardar_resposta
from validacao import consulta_valida, registro_valido

# Porta padrão de escuta dos servidores DNS internos
PORT = 5300

def load_db(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar banco de dados local: {e}")
        return {}

# TODO 4. Se a requisição for RECURSIVA (rd = True) e o nó intermediário precisar buscar recursiva
def query_upstream(server_ip, qname, qtype, rd):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    query = {
        "transaction_id": secrets.randbits(16),
        "qname": qname,
        "qtype": qtype,
        "rd": rd
    }
    try:
        sock.sendto(json.dumps(query).encode('utf-8'), (server_ip, PORT))
        return aguardar_resposta(sock, query, (server_ip, PORT), 3.0)
    except Exception as e:
        print(f"Erro ao consultar servidor upstream ({server_ip}): {e}")
        return None
    finally:
        sock.close()

def resolver_consulta(query, db):
    if not consulta_valida(query):
        print("[DNS] Consulta descartada: ID, domínio ou rd inválido.")
        return None
    tx_id = query.get("transaction_id")
    qname = query.get("qname")
    qtype = query.get("qtype", "A")
    recursion_desired = query["rd"]
    response = {
        "transaction_id": tx_id,
        "status": "NXDOMAIN",
        "answer": [],
        "authority": []
    }

    if qtype not in ("A", "NS"):
        return response

    # TODO: 1. Verificar se o domínio exato ou sufixo correspondente está no DB local
    record = db.get(qname)
    if registro_valido(record) and record["type"] == qtype:
        # TODO: 2. Preencher a "answer" e retornar "SUCCESS" para o nome e tipo solicitados
        response["status"] = "SUCCESS"
        response["answer"].append({
            "name": qname,
            "type": qtype,
            "value": record["value"],
            "ttl": record.get("ttl", 300 if qtype == "NS" else 60)
        })
        return response

    # Procura o sufixo mais específico que indica outro servidor.
    matched_key = None
    for key in db:
        record = db[key]
        if not registro_valido(record) or record["type"] != "NS":
            continue
        if qname == key or qname.endswith("." + key):
            if matched_key is None or len(key) > len(matched_key):
                matched_key = key
    if matched_key is None:
        return response

    record = db[matched_key]
    # TODO: 3. Se a requisição for ITERATIVA (rd = False), preencher "authority"
    if not recursion_desired:
        response["status"] = "REFERRAL"
        response["authority"].append({
            "name": matched_key,
            "type": "NS",
            "value": record["value"],
            "ttl": record.get("ttl", 300)
        })
    else:
        # TODO: 4. Se a requisição for RECURSIVA (rd = True), consultar o próximo servidor
        upstream_resp = query_upstream(record["value"], qname, qtype, rd=True)
        if upstream_resp:
            response = upstream_resp
            response["transaction_id"] = tx_id

    return response

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
            response = resolver_consulta(query, db)
            if response is None:
                continue
        
            # Envia a resposta JSON de volta ao solicitante
            sock.sendto(json.dumps(response).encode('utf-8'), addr)

        except Exception as e:
            print(f"Erro ao processar pacote de {addr}: {e}")
    
if __name__ == "__main__":
    main()
