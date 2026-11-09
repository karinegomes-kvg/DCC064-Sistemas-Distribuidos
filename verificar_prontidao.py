import sys


def porta_pronta(porta):
    # Consulta a tabela do Linux sem enviar pacotes nem consumir a cota do servidor.
    porta_hex = f"{porta:04X}"
    try:
        with open("/proc/net/udp") as arquivo:
            next(arquivo, None)
            for linha in arquivo:
                campos = linha.split()
                if len(campos) > 1 and campos[1].split(":")[-1] == porta_hex:
                    return True
    except OSError:
        return False
    return False


if __name__ == "__main__":
    sys.exit(0 if porta_pronta(int(sys.argv[1])) else 1)
