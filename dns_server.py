import socket
import json
import os
import secrets
from atendimento import atender_consultas
from comunicacao import aguardar_resposta
from prazos import criar_prazo, tempo_restante
from validacao import consulta_valida, registro_valido
from logger_config import configurar_logger #logs

# Porta padrão de escuta dos servidores DNS internos
PORT = 5300

# Adicionando logs para o dns_server
logger = configurar_logger(os.getenv("DNS_ROLE", "DNS"))

def load_db(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao carregar banco de dados local: {e}")
        return {}

# TODO 4. Se a requisição for RECURSIVA (rd = True) e o nó intermediário precisar buscar recursiva
def query_upstream(server_ip, qname, qtype, rd, prazo=None):
    if prazo is None:
        prazo = criar_prazo()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    query = {
        "transaction_id": secrets.randbits(16),
        "qname": qname,
        "qtype": qtype,
        "rd": rd
    }
    try:
        restante = tempo_restante(prazo)
        query["tempo_restante"] = restante
        sock.settimeout(restante)
        
        logger.debug(
        f"Enviando consulta para {server_ip}: "
        f"{qname} ({qtype}), ID={query['transaction_id']}, RD={rd}"
        )

        sock.sendto(json.dumps(query).encode('utf-8'), (server_ip, PORT))
        response = aguardar_resposta(sock, query, (server_ip, PORT), prazo=prazo)

        logger.debug(
        f"Resposta recebida de {server_ip}: status={response.get('status')}"
        )
        return response

    except socket.timeout:
        raise
    except Exception as e:
        logger.error(f"Erro ao consultar servidor upstream ({server_ip}): {e}")
        return None
    finally:
        sock.close()

def resolver_consulta(query, db, prazo=None):
    if not consulta_valida(query):
        logger.error("[DNS] Consulta descartada: ID, domínio, rd ou tempo restante inválido.")
        return None
    if prazo is None:
        prazo = criar_prazo(query)
    try:
        tempo_restante(prazo)
    except socket.timeout:
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
        try:
            upstream_resp = query_upstream(record["value"], qname, qtype, rd=True, prazo=prazo)
        except socket.timeout:
            logger.error(f"[DNS] Prazo total esgotado para '{qname}' ({qtype}).")
            return None
        if upstream_resp:
            response = upstream_resp
            response["transaction_id"] = tx_id

    return response

def main():
    # Carrega o papel e banco de dados via variáveis de ambiente
    role = os.getenv("DNS_ROLE", "ROOT") # ROOT, TLD ou AUTH
    db_path = os.getenv("DB_PATH", "records.json")
    db = load_db(db_path)

    def processar_consulta(query, prazo=None):
        return resolver_consulta(query, db, prazo=prazo)

    # Configura o Socket UDP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))

    logger.info(f"Servidor Mini-DNS iniciado como papel [{role}] escutando na porta {PORT}...")
    try:
        atender_consultas(sock, processar_consulta, role)
    finally:
        sock.close()
    
if __name__ == "__main__":
    main()
