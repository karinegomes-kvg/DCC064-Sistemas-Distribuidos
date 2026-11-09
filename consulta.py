
import argparse
import socket
import json
import secrets
import time

HOST = "127.0.0.1"
PORT = 5354

def consultar_dns(dominio, tipo="A", recursivo=False):
    consulta = {
        "transaction_id": secrets.randbits(16),
        "qname": dominio,
        "qtype": tipo,
        "rd": recursivo,
        "tempo_restante": 5.0
    }

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(6.0)

        inicio = time.perf_counter()

        sock.sendto(
            json.dumps(consulta).encode("utf-8"),
            (HOST, PORT)
        )

        try:
            dados, endereco = sock.recvfrom(4096)
            tempo = (time.perf_counter() - inicio) * 1000

            resposta = json.loads(dados.decode("utf-8"))

            print(json.dumps(resposta, indent=4, ensure_ascii=False))
            print(f"\nTempo de resposta: {tempo:.3f} ms")

            return resposta

        except socket.timeout:
            print("Timeout: o resolver não respondeu.")
            return None

def main():
    parser = argparse.ArgumentParser(description="Cliente Mini-DNS para consultas A e NS")
    parser.add_argument(
        "dominio",
        nargs="?",
        default="www.ufjf.br",
        help="Domínio a consultar (padrão: www.ufjf.br)",
    )
    parser.add_argument("--qtype", choices=("A", "NS"), default="A")
    parser.add_argument(
        "--recursiva",
        action="store_true",
        help="Solicita resolução recursiva pelo Root",
    )
    argumentos = parser.parse_args()
    consultar_dns(argumentos.dominio, argumentos.qtype, argumentos.recursiva)


if __name__ == "__main__":
    main()
