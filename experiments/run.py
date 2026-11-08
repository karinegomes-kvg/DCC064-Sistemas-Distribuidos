"""Laboratório UDP em loopback: python -m experiments.run --quick."""
import argparse
import contextlib
import csv
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import selectors
import socket
import sys
import time

from comunicacao import aguardar_resposta

HOSTS = dict(root="127.0.0.100", tld="127.0.0.101",
             auth="127.0.0.102", resolver="127.0.0.50")


def server(role, port, workers, cache, protected, logfile, ready):
    # Processos distintos: cache, GIL, filas e cotas independentes por servidor.
    import dns_server
    import resolver
    from atendimento import atender_consultas
    with open(logfile, "w", encoding="utf-8", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            resolver.ROOT_DNS_IP = HOSTS["root"]
            resolver.SERVER_PORT = dns_server.PORT = port
            ttl = 0 if cache == "cold" else 600
            databases = {
                "root": {"br": {"type": "NS", "value": HOSTS["tld"]}},
                "tld": {"ufjf.br": {"type": "NS", "value": HOSTS["auth"]}},
                "auth": {"www.ufjf.br": {"type": "A", "value": "172.20.0.40", "ttl": ttl}},
            }
            def handle(query, prazo=None):
                if role == "resolver":
                    return resolver.resolver_consulta(query, prazo=prazo)
                return dns_server.resolver_consulta(query, databases[role], prazo=prazo)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind((HOSTS[role], port))
                ready.send(True)
                ready.close()
                atender_consultas(sock, handle, role, trabalhadores=workers,
                                  limite_fila=8,
                                  limite_por_ip=(20 if role == "resolver" else 100) if protected else 100000,
                                  limite_global=(100 if role == "resolver" else 200) if protected else 100000)


@contextlib.contextmanager
def topology(folder, workers, cache, protected):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((HOSTS["root"], 0))
        port = probe.getsockname()[1]
    processes = []
    try:
        for role in HOSTS:
            recv, send = mp.Pipe(duplex=False)
            proc = mp.Process(target=server, args=(role, port, workers, cache, protected,
                                                  str(folder / f"{role}.log"), send))
            proc.start()
            processes.append(proc)
            send.close()
            try:
                if not recv.poll(10) or not recv.recv():
                    raise RuntimeError(f"Falha ao iniciar {role}; consulte os logs")
            finally:
                recv.close()
        # Prova fim a fim e aquecimento fora das amostras.
        query = dict(transaction_id=65535, qname="www.ufjf.br", qtype="A", rd=True)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.2", 0))
            endpoint = (HOSTS["resolver"], port)
            sock.sendto(json.dumps(query).encode(), endpoint)
            answer = aguardar_resposta(sock, query, endpoint, tempo_limite=6)
            if answer["status"] != "SUCCESS":
                raise RuntimeError("A topologia não resolveu o registro de controle")
        time.sleep(1.05)  # Libera cotas da prontidão, preservando cache quente.
        yield port
        if any(not p.is_alive() for p in processes):
            raise RuntimeError("Um servidor encerrou durante o experimento")
    finally:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
        for proc in processes:
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()


def traffic(endpoint, duration, rate, attack_rate, sources, recursive, timeout):
    """Carga aberta limitada; respostas não bloqueiam a emissão de consultas."""
    # O temporizador padrão do Windows pode arredondar select(2 ms) para ~16 ms.
    # A resolução é restaurada mesmo se o gerador falhar.
    if sys.platform == "win32":
        import ctypes
        timer = ctypes.WinDLL("winmm")
        if timer.timeBeginPeriod(1) != 0:
            raise RuntimeError("Não foi possível ajustar o temporizador do gerador")
    else:
        timer = None
    try:
        return _traffic(endpoint, duration, rate, attack_rate, sources, recursive, timeout)
    finally:
        if timer is not None:
            timer.timeEndPeriod(1)


def _traffic(endpoint, duration, rate, attack_rate, sources, recursive, timeout):
    rows, pending, streams = [], {}, []
    with selectors.DefaultSelector() as selector:
        for index in range(1 + sources):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("127.0.0.2" if index == 0 else f"127.0.0.{10 + index}", 0))
                sock.setblocking(False)
                selector.register(sock, selectors.EVENT_READ)
            except BaseException:
                sock.close()
                raise
            streams.append([sock, rate if index == 0 else attack_rate / sources,
                            "legitimate" if index == 0 else "attack", 0.0])
        start = time.perf_counter()
        end = start + duration
        tx = 0
        try:
            while time.perf_counter() < end or pending:
                now = time.perf_counter()
                if now < end:
                    for stream in streams:
                        sock, rps, kind, due = stream
                        if rps <= 0 or now < due:
                            continue
                        tx += 1
                        query = dict(transaction_id=tx, qname="www.ufjf.br", qtype="A", rd=recursive)
                        row = dict(traffic=kind, source=sock.getsockname()[0], transaction_id=tx,
                                   sent_s=now - start, latency_ms=None, status="TIMEOUT")
                        try:
                            sock.sendto(json.dumps(query).encode(), endpoint)
                            pending[(sock.fileno(), tx)] = (now, row, query)
                        except OSError:
                            row["status"] = "SEND_ERROR"
                        rows.append(row)
                        # Não compensa atraso com rajadas: taxa realizada é registrada.
                        stream[3] = now + 1 / rps
                wake = min([end] if now < end else [now + .01])
                if now < end:
                    wake = min(wake, *(s[3] for s in streams if s[1] > 0))
                for key, _ in selector.select(max(0, min(.002, wake - now))):
                    sock = key.fileobj
                    try:
                        data, origin = sock.recvfrom(4096)
                        response = json.loads(data)
                        if not isinstance(response, dict) or origin != endpoint:
                            continue
                        item = pending.get((sock.fileno(), response.get("transaction_id")))
                        if item is None:
                            continue
                        from validacao import resposta_valida
                        if not resposta_valida(response):
                            continue
                        sent, row, query = item
                        if response["status"] == "SUCCESS" and any(
                            a["name"] != query["qname"] or a["type"] != query["qtype"]
                            for a in response["answer"]
                        ):
                            continue
                        received = time.perf_counter()
                        if received - sent <= timeout:
                            row.update(status=response["status"], latency_ms=(received - sent) * 1000)
                            del pending[(sock.fileno(), response["transaction_id"])]
                    except (OSError, ValueError, TypeError):
                        continue
                now = time.perf_counter()
                for key, (sent, _, _) in list(pending.items()):
                    if now - sent >= timeout:
                        del pending[key]
        finally:
            for sock, *_ in streams:
                sock.close()
    return rows


def cases(quick, suites):
    targets = ["resolver"] if quick else list(HOSTS)
    if "resolution" in suites:
        for mode in (False, True):
            for cache in ("cold", "hot"):
                yield "resolution", "resolver", mode, cache, 4, 10, False, 0
    if "load" in suites:
        for target in targets:
            for rate in ([20, 100] if quick else [10, 100, 300]):
                yield "load", target, True, "cold", 4, rate, False, 0
    if "scale" in suites:
        for workers in ([1, 4] if quick else [1, 4, 8]):
            yield "scale", "resolver", True, "cold", workers, 300, False, 0
    for suite, sources in (("dos", 1), ("ddos", 8)):
        if suite in suites:
            for target in targets:
                for protected in (False, True):
                    yield suite, target, True, "cold", 4, 10, protected, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Triagem curta, somente alvo resolver")
    parser.add_argument("--suites", nargs="+", choices=["resolution", "load", "scale", "dos", "ddos"],
                        default=["resolution", "load", "scale", "dos", "ddos"])
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--attack-rate", type=int, default=300, help="QPS agregado, máximo 1000")
    parser.add_argument("--timeout", type=float, default=6)
    parser.add_argument("--output", type=Path, default=Path("results") / time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    duration = args.duration if args.duration is not None else (1 if args.quick else 5)
    repetitions = args.repetitions if args.repetitions is not None else (1 if args.quick else 3)
    if not (0 < duration <= 30 and 1 <= repetitions <= 10 and 1 <= args.attack_rate <= 1000
            and 0 < args.timeout <= 6):
        parser.error("Use duração (0,30], repetições [1,10], taxa [1,1000], timeout (0,6]")
    args.output.mkdir(parents=True, exist_ok=False)
    import subprocess
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    metadata = dict(python=sys.version, platform=platform.platform(), revision=revision,
                    duration=duration, repetitions=repetitions, attack_rate=args.attack_rate,
                    timeout=args.timeout, quick=args.quick, suites=args.suites,
                    topology=HOSTS, queue=8, quotas_protected=dict(resolver=[20, 100], dns=[100, 200]),
                    quotas_unprotected=[100000, 100000], clock="perf_counter",
                    windows_timer_ms=1 if sys.platform == "win32" else None,
                    note="Loopback multiprocess; DDoS simulado por IPs locais; escala vertical")
    metadata["source_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in [*Path('.').glob('*.py'), *Path('experiments').glob('*.py')]}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    fields = ["case", "suite", "target", "mode", "cache", "workers", "protected", "rate",
              "attack_rate", "sources", "repetition", "phase", "duration_s", "traffic", "source",
              "transaction_id", "sent_s", "latency_ms", "status"]
    with (args.output / "requests.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        number = 0
        for suite, target, recursive, cache, workers, rate, protected, sources in cases(args.quick, args.suites):
            for repetition in range(1, repetitions + 1):
                number += 1
                folder = args.output / f"case-{number:03}"
                folder.mkdir()
                print(f"{number}: {suite} {target} workers={workers} cache={cache} rd={recursive}", flush=True)
                with topology(folder, workers, cache, protected) as port:
                    phases = ["baseline", "attack", "recovery"] if sources else ["measure"]
                    for phase in phases:
                        active_sources = sources if phase == "attack" else 0
                        rows = traffic((HOSTS[target], port), duration, rate,
                                       args.attack_rate if active_sources else 0,
                                       active_sources, recursive, args.timeout)
                        for row in rows:
                            writer.writerow(dict(case=number, suite=suite, target=target,
                                                 mode="recursive" if recursive else "iterative",
                                                 cache=cache, workers=workers, protected=protected, rate=rate,
                                                 attack_rate=args.attack_rate if active_sources else 0,
                                                 sources=active_sources, repetition=repetition,
                                                 phase=phase, duration_s=duration, **row))
                        file.flush()
                        # Drena respostas pendentes e libera janela de cotas antes da próxima fase.
                        if sources:
                            time.sleep(1.05)
    from experiments.report import generate
    generate(args.output)
    print(f"Resultados: {args.output.resolve()}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
