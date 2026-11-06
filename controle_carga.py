import time
from collections import deque


class ControleCarga:
    def __init__(self, limite_por_ip, limite_global):
        if type(limite_por_ip) is not int or limite_por_ip <= 0:
            raise ValueError("O limite por IP precisa ser um inteiro positivo")
        if type(limite_global) is not int or limite_global <= 0:
            raise ValueError("O limite global precisa ser um inteiro positivo")
        self.limite_por_ip = limite_por_ip
        self.limite_global = limite_global
        self.recentes = deque()
        self.por_ip = {}
        self.descartados_por_ip = 0
        self.descartados_globais = 0

    def aceitar(self, ip):
        agora = time.monotonic()

        # Remove as admissões que já saíram da janela móvel de 1 segundo.
        while self.recentes and self.recentes[0][0] <= agora - 1.0:
            _, ip_antigo = self.recentes.popleft()
            self.por_ip[ip_antigo] -= 1
            if self.por_ip[ip_antigo] == 0:
                del self.por_ip[ip_antigo]

        if len(self.recentes) >= self.limite_global:
            self.descartados_globais += 1
            return False
        if self.por_ip.get(ip, 0) >= self.limite_por_ip:
            self.descartados_por_ip += 1
            return False

        # Somente pacotes admitidos ocupam memória e cota para interpretação do JSON.
        self.recentes.append((agora, ip))
        self.por_ip[ip] = self.por_ip.get(ip, 0) + 1
        return True
