"""CLI: python -m automail {backup|migrate|run} [opções]."""

from __future__ import annotations

import argparse
import sys

from .accounts import load_accounts
from .config import Settings, load_dotenv
from .core import backup_account, check_account, migrate_account
from .oauth import TokenProvider


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="automail",
        description="Backup de contas Titan e migração para o Microsoft 365 via IMAP.",
    )
    p.add_argument("--env", default=".env", help="Arquivo .env (padrão: .env)")
    p.add_argument("--csv", help="Caminho do CSV de contas (sobrescreve o .env)")
    p.add_argument(
        "--only",
        help="Migra/backup apenas a conta cujo e-mail Titan corresponde a este valor",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="Só baixa as contas Titan para o disco")
    sub.add_parser("migrate", help="Só sobe backups existentes para o M365")
    sub.add_parser("run", help="Backup e depois migração (fluxo completo)")
    sub.add_parser(
        "check", help="Testa login Titan, token OAuth e login M365 (não move e-mails)"
    )
    return p


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
    for account in accounts:
        try:
            if do_check and token_provider is not None:
                if not check_account(account, settings, token_provider):
                    failures += 1
                continue
            if do_backup:
                backup_account(account, settings)
            if do_migrate and token_provider is not None:
                migrate_account(account, settings, token_provider)
        except Exception as exc:  # noqa: BLE001 — queremos continuar as demais contas
            failures += 1
            print(f"  ✗ ERRO em {account.titan_email}: {exc}", file=sys.stderr)

    print(f"\nFinalizado. {len(accounts) - failures}/{len(accounts)} conta(s) OK.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
