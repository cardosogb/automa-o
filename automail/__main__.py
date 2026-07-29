"""CLI: python -m automail {backup|migrate|run|check} [opções]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .accounts import load_accounts
from .config import Settings, load_dotenv
from .core import backup_account, check_account, migrate_account
from .oauth import TokenProvider

# Arquivo gerado pelo 'check' com os e-mails Titan que passaram em tudo.
CONTAS_OK_FILE = "contas_ok.txt"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="automail",
        description="Backup de contas Titan e migração para o Microsoft 365 via IMAP.",
    )
    p.add_argument("--env", default=".env", help="Arquivo .env (padrão: .env)")
    p.add_argument("--csv", help="Caminho do CSV de contas (sobrescreve o .env)")
    p.add_argument(
        "--only",
        help="Processa apenas a conta cujo e-mail Titan corresponde a este valor",
    )
    p.add_argument(
        "--lista",
        help=(
            "Arquivo com e-mails Titan (um por linha); processa só essas contas. "
            f"O 'check' gera {CONTAS_OK_FILE} com as contas aprovadas — use "
            f"'--lista {CONTAS_OK_FILE}' no run para migrar só as que passaram."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="Só baixa as contas Titan para o disco")
    sub.add_parser("migrate", help="Só sobe backups existentes para o M365")
    sub.add_parser("run", help="Backup e depois migração (fluxo completo)")
    sub.add_parser(
        "check", help="Testa login Titan, token OAuth e login M365 (não move e-mails)"
    )
    return p


def _load_lista(path: str) -> set[str]:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"Lista não encontrada: {p}")
    return {
        line.strip().lower()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    load_dotenv(args.env)
    settings = Settings.from_env()
    if args.csv:
        settings.accounts_csv = args.csv

    accounts = load_accounts(settings.accounts_csv)
    if args.only:
        accounts = [a for a in accounts if a.titan_email.lower() == args.only.lower()]
        if not accounts:
            print(f"Nenhuma conta corresponde a --only={args.only}")
            return 1
    if args.lista:
        permitidos = _load_lista(args.lista)
        antes = len(accounts)
        accounts = [a for a in accounts if a.titan_email.lower() in permitidos]
        print(f"Filtro --lista {args.lista}: {len(accounts)}/{antes} conta(s).")
        if not accounts:
            print("Nenhuma conta do CSV está na lista informada.")
            return 1

    do_backup = args.command in ("backup", "run")
    do_migrate = args.command in ("migrate", "run")
    do_check = args.command == "check"

    token_provider = None
    if do_migrate or do_check:
        settings.require_oauth()
        token_provider = TokenProvider(
            settings.azure_tenant_id,
            settings.azure_client_id,
            settings.azure_client_secret,
        )

    failures = 0
    aprovadas: list[str] = []
    for account in accounts:
        try:
            if do_check and token_provider is not None:
                if check_account(account, settings, token_provider):
                    aprovadas.append(account.titan_email)
                else:
                    failures += 1
                continue
            if do_backup:
                backup_account(account, settings)
            if do_migrate and token_provider is not None:
                migrate_account(account, settings, token_provider)
        except Exception as exc:  # noqa: BLE001 — queremos continuar as demais contas
            failures += 1
            print(f"  ✗ ERRO em {account.titan_email}: {exc}", file=sys.stderr)

    if do_check:
        Path(CONTAS_OK_FILE).write_text(
            "\n".join(aprovadas) + ("\n" if aprovadas else ""), encoding="utf-8"
        )
        print(
            f"\nResumo do check: {len(aprovadas)} OK, {failures} com falha "
            f"(de {len(accounts)})."
        )
        print(f"Contas aprovadas salvas em {CONTAS_OK_FILE}.")
        if aprovadas:
            print(f"Para migrar só as aprovadas:  "
                  f"python3 -m automail run --lista {CONTAS_OK_FILE}")
    else:
        print(f"\nFinalizado. {len(accounts) - failures}/{len(accounts)} conta(s) OK.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
