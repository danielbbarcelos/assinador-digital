#!/usr/bin/env python3
"""Valida as assinaturas de um PDF, offline, contra as ACs de ``certs/``.

    .venv/bin/python tools/validar.py documento-assinado.pdf

A mesma validação que a aba "Validar" do app faz; a lógica mora em
``app/validation.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.validation import validate_pdf_bytes  # noqa: E402

CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"

VERDE, VERMELHO, AMARELO, CINZA, FIM = (
    "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m",
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    caminho = Path(argv[1])
    if not caminho.is_file():
        print(f"{VERMELHO}Arquivo não encontrado: {caminho}{FIM}")
        return 1

    relatorios = validate_pdf_bytes(caminho.read_bytes(), CERTS_DIR)
    if not relatorios:
        print(f"{AMARELO}Nenhuma assinatura neste PDF.{FIM}")
        return 1

    problemas = 0
    for i, r in enumerate(relatorios, start=1):
        print("─" * 72)
        rotulo = "Carimbo do tempo" if r.kind == "timestamp" else f"Assinatura {i}"
        print(f"{rotulo}: {r.field_name}")
        print(f"  {'autoridade ' if r.kind == 'timestamp' else 'titular    '}  {r.holder}")
        if r.document:
            print(f"  documento    {r.document}")
        print(f"  íntegro      {_sim_nao(r.intact)}   (não foi alterado depois)")
        print(f"  confiável    {_sim_nao(r.trusted)}   (cadeia até uma AC conhecida)")
        print(f"  abrange      {r.coverage_label}")
        print(f"  alterações   {r.modifications or '—'}")
        print(f"  data         {r.signed_at or r.timestamped_at}")
        if r.kind == "signature":
            print(
                f"  carimbo      {_sim_nao(True) + f' ({r.timestamped_at})' if r.timestamped_at else CINZA + 'sem carimbo do tempo' + FIM}"
            )
        print(f"\n  {r.summary}")
        if not r.ok:
            problemas += 1

    print("─" * 72)
    if problemas:
        print(f"{VERMELHO}{problemas} assinatura(s) com problema.{FIM}")
        return 1
    print(f"{VERDE}Todas as assinaturas estão íntegras e confiáveis.{FIM}")
    return 0


def _sim_nao(valor: bool) -> str:
    return f"{VERDE}sim{FIM}" if valor else f"{VERMELHO}não{FIM}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
