"""Agrega CSV por execução e gera gráficos: python -m experiments.report results/PASTA."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import statistics


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def summarize(rows):
    first = rows[0]
    duration = float(first["duration_s"])
    successful = [float(r["latency_ms"]) for r in rows if r["status"] == "SUCCESS"]
    counts = Counter(r["status"] for r in rows)
    return {**{k: first[k] for k in ("case", "suite", "target", "mode", "cache", "workers",
                                    "protected", "rate", "attack_rate", "sources", "repetition", "phase", "traffic")},
            "sent": len(rows), "success": counts["SUCCESS"], "timeouts": counts["TIMEOUT"],
            "other": len(rows) - counts["SUCCESS"] - counts["TIMEOUT"],
            "offered_qps": len(rows) / duration,
            "successful_qps": len(successful) / duration,
            "failure_pct": 100 * (len(rows) - len(successful)) / len(rows),
            "p50_ms": percentile(successful, .5), "p95_ms": percentile(successful, .95),
            "p99_ms": percentile(successful, .99)}


def generate(folder):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with (folder / "requests.csv").open(encoding="utf-8", newline="") as file:
        groups = defaultdict(list)
        for row in csv.DictReader(file):
            groups[(row["case"], row["phase"], row["traffic"])].append(row)
    summary = [summarize(rows) for rows in groups.values()]
    if not summary:
        raise ValueError("Nenhuma amostra no CSV")
    with (folder / "summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    for suite in ("resolution", "load", "scale", "dos", "ddos"):
        selected = [r for r in summary if r["suite"] == suite and r["traffic"] == "legitimate"]
        if not selected:
            continue
        targets = list(dict.fromkeys(r["target"] for r in selected)) if suite in ("dos", "ddos", "load") else [None]
        fig, axes = plt.subplots(len(targets), 3, squeeze=False, figsize=(14, 4.4 * len(targets)))
        for target, panels in zip(targets, axes):
            grouped = defaultdict(list)
            for row in selected:
                if target is not None and row["target"] != target:
                    continue
                label = (f'{row["mode"]}\n{row["cache"]}' if suite == "resolution" else
                         f'{row["rate"]} QPS' if suite == "load" else
                         f'{row["workers"]} workers' if suite == "scale" else
                         ("protegido" if row["protected"] == "True" else "cotas altas")
                         + f'\n{row["phase"]}')
                grouped[label].append(row)
            for ax, metric, title in zip(panels, ["p95_ms", "successful_qps", "failure_pct"],
                                         ["Latência p95 dos sucessos (ms)", "Consultas bem-sucedidas/s", "Falhas (%)"]):
                labels = list(grouped)
                values = [[r[metric] for r in grouped[label] if r[metric] is not None] for label in labels]
                means = [statistics.mean(v) if v else 0 for v in values]
                errors = [statistics.stdev(v) if len(v) > 1 else 0 for v in values]
                ax.bar(range(len(labels)), means, yerr=errors, capsize=3, color="#277da1")
                for i, v in enumerate(values):
                    if not v:
                        ax.text(i, 0, "sem sucesso", rotation=90, va="bottom", ha="center", fontsize=8)
                ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right", fontsize=9)
                ax.set_title(f"{target}: {title}" if target else title)
                ax.set_ylim(bottom=0)
                if metric == "failure_pct":
                    ax.set_ylim(0, 100)
                ax.grid(axis="y", alpha=.2)
        fig.suptitle(f"Mini-DNS — {suite} — média ± desvio padrão entre repetições")
        fig.tight_layout()
        fig.savefig(folder / f"{suite}.png", dpi=150)
        fig.savefig(folder / f"{suite}.svg")
        plt.close(fig)
    lines = ["# Resultados do laboratório Mini-DNS", "",
             "Métricas por execução em `summary.csv`; amostras em `requests.csv`; configuração em `metadata.json`.",
             "", "| Caso | Suíte/alvo | Modo/cache | Protegido | Fase | QPS realizado | Sucessos/s | Falhas % | p95 ms |",
             "|---|---|---|---|---|---:|---:|---:|---:|"]
    for r in summary:
        if r["traffic"] != "legitimate":
            continue
        p95 = f'{r["p95_ms"]:.3f}' if r["p95_ms"] is not None else "n/a"
        lines.append(f'| {r["case"]} | {r["suite"]}/{r["target"]} | {r["mode"]}/{r["cache"]} | '
                     f'{r["protected"]} | {r["phase"]} | {r["offered_qps"]:.1f} | {r["successful_qps"]:.1f} | '
                     f'{r["failure_pct"]:.1f} | {p95} |')
    lines += ["", "Os percentis excluem timeouts e erros: examine sempre a taxa de falhas ao lado da latência.",
              "Sucessos/s = respostas válidas às consultas emitidas na janela / duração da janela; inclui drenagem após a emissão.",
              "A emissão usa um laço aberto com taxa máxima; atrasos do gerador reduzem o QPS realizado, sem rajadas de compensação.",
              "Recuperação é medida após drenar as consultas e esperar mais 1,05 s; não mede recuperação instantânea.",
              "IPs de loopback simulam fontes distintas, sem máquinas ou enlaces distribuídos reais.",
              "Escala varia trabalhadores de todos os nós (escala vertical), mantendo fila de 8 e topologia fixa.",
              "O perfil sem proteção usa cotas de 100000/s; fila e prazos continuam ativos.",
              "Logs permitem observar descartes por cota e fila; timeout isolado não identifica sua causa.",
              "Valores são específicos desta máquina, com gerador e quatro servidores compartilhando CPU e sistema operacional."]
    (folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    metadata_path = folder / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["report_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    generate(parser.parse_args().folder)
