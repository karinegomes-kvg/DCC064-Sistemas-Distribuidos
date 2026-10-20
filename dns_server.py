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

# TODO 4. Se a requisição for RECURSIVA (rd = True) e o nó intermediário precisar buscar recursiva
def query_upstream(server_ip, qname, qtype, rd):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    query = {
        "transaction_id": 9999,
        "qname": qname,
        "qtype": qtype,
        "rd": rd
    }
    try:
        sock.sendto(json.dumps(query).encode('utf-8'), (server_ip, PORT))
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode('utf-8'))
    except Exception as e:
        print(f"Erro ao consultar servidor upstream ({server_ip}): {e}")
        return None
    finally:
        sock.close()

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
            matched_key = None
            if qname in db:
                matched_key = qname
            else:
                for key in db.keys():
                    if qname.endswith("." + key) or qname == key:
                        matched_key = key
                        break
            if matched_key:
                record = db[matched_key]

                # TODO: 2. Se estiver e for o servidor Autorizativo final, preencher a "answer" e retornar "SUCCESS"
                if record["type"] == "A":
                    response["status"] = "SUCCESS"
                    response["answer"].append({
                        "name": qname,
                        "type": record["type"],
                        "value": record["value"],
                        "ttl": record.get("ttl", 60)
                    })

                # TODO: 3. Se for ROOT ou TLD e a requisição for ITERATIVA (rd = False), preencher "authority" com
                elif record["type"] == "NS":
                    if not recursion_desired:
                        response["status"] = "REFERRAL"
                        response["authority"].append({
                            "name": matched_key,
                            "type": "NS",
                            "value": record["value"],
                            "ttl": record.get("ttl", 300)
                        })

                    # TODO: 4. Se a requisição for RECURSIVA (rd = True) e o nó intermediário precisar buscar recursiva
                    else:
                        upstream_resp = query_upstream(record["value"], qname, qtype, rd=True)
                        if upstream_resp:
                            response = upstream_resp
                            response["transaction_id"] = tx_id
        
            # Envia a resposta JSON de volta ao solicitante
            sock.sendto(json.dumps(response).encode('utf-8'), addr)

        except Exception as e:
            print(f"Erro ao processar pacote de {addr}: {e}")
    
if __name__ == "__main__":
    main()