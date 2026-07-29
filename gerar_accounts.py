#!/usr/bin/env python3
"""Gera o accounts.csv a partir de uma lista de prefixos (prefixos.txt).

Padrão de cada conta:
    origem : <prefixo>@<dominio-origem>
    destino: <prefixo>@<dominio-destino>
    senha  : a mesma para todas (pedida de forma segura, não fica no disco/git)

Uso:
    python3 gerar_accounts.py
    python3 gerar_accounts.py --prefixos prefixos.txt --saida accounts.csv
    python3 gerar_accounts.py --origem fernandomiranda.com.br \
        --destino fernandomirandaadvocacia.onmicrosoft.com

A senha pode vir da variável de ambiente TITAN_PASSWORD; se não existir, o
script pergunta (sem exibir o que você digita).
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Gera accounts.csv a partir de prefixos.")
    ap.add_argument("--prefixos", default="prefixos.txt", help="Arquivo com um prefixo por linha")
    ap.add_argument("--saida", default="accounts.csv", help="CSV de saída")
    ap.add_argument("--origem", default="fernandomiranda.com.br", help="Domínio de origem (Titan)")
    ap.add_argument(
        "--destino",
        default="fernandomirandaadvocacia.onmicrosoft.com",
        help="Domínio de destino (Microsoft 365)",
    )
    ap.add_argument(
        "--excecoes",
        default="destinos_especiais.txt",
        help=(
            "Arquivo opcional com exceções de destino, uma por linha no formato "
            "'prefixo=email_destino_completo'. Usado quando a caixa do M365 não "
            "segue o padrão <prefixo>@<destino>."
        ),
    )
    ap.add_argument(
        "--forcar",
        action="store_true",
        help="Sobrescreve o accounts.csv se ele já existir",
    )
    args = ap.parse_args()

    prefixos_path = Path(args.prefixos)
    if not prefixos_path.is_file():
        print(f"Arquivo de prefixos não encontrado: {prefixos_path}", file=sys.stderr)
        return 1

    prefixos: list[str] = []
    vistos: set[str] = set()
    duplicados: list[str] = []
    for linha in prefixos_path.read_text(encoding="utf-8").splitlines():
        p = linha.strip()
        if not p or p.startswith("#"):
            continue
        if p in vistos:
            duplicados.append(p)
            continue
        vistos.add(p)
        prefixos.append(p)

    if not prefixos:
        print("Nenhum prefixo válido no arquivo.", file=sys.stderr)
        return 1

    # Exceções de destino: prefixo -> e-mail de destino completo.
    excecoes: dict[str, str] = {}
    exc_path = Path(args.excecoes)
    if exc_path.is_file():
        for linha in exc_path.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            chave, valor = chave.strip(), valor.strip()
            if chave and valor:
                excecoes[chave] = valor
        if excecoes:
            print(f"Exceções de destino carregadas: {len(excecoes)} "
                  f"({', '.join(excecoes)})")

    saida = Path(args.saida)
    if saida.exists() and not args.forcar:
        print(
            f"{saida} já existe. Use --forcar para sobrescrever "
            "(ou apague o arquivo antes).",
            file=sys.stderr,
        )
        return 1

    senha = os.environ.get("TITAN_PASSWORD")
    if not senha:
        senha = getpass.getpass("Senha (a mesma para todas as contas): ")
    if not senha:
        print("Senha vazia — abortando.", file=sys.stderr)
        return 1

    with saida.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["titan_email", "titan_password", "target_email"])
        for p in prefixos:
            destino = excecoes.get(p, f"{p}@{args.destino}")
            writer.writerow([f"{p}@{args.origem}", senha, destino])

    print(f"OK: {saida} gerado com {len(prefixos)} conta(s).")
    if duplicados:
        print(f"Aviso: {len(duplicados)} prefixo(s) duplicado(s) ignorado(s): "
              + ", ".join(duplicados))
    print("Confira com:  cat", saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
