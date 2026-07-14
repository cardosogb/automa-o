"""Leitura do CSV de contas a migrar."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Account:
    titan_email: str
    titan_password: str
    target_email: str

    @property
    def local_prefix(self) -> str:
        """Texto antes do @ — usado como nome da pasta de backup."""
        return self.titan_email.split("@", 1)[0].strip().lower()


_REQUIRED = {"titan_email", "titan_password", "target_email"}


def load_accounts(csv_path: str | Path) -> list[Account]:
    path = Path(csv_path)
    if not path.is_file():
        raise SystemExit(
            f"Arquivo de contas não encontrado: {path}\n"
            "Copie accounts.example.csv para accounts.csv e preencha."
        )
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = _REQUIRED - headers
        if missing:
            raise SystemExit(
                f"CSV sem as colunas obrigatórias: {', '.join(sorted(missing))}.\n"
                f"Cabeçalho esperado: {', '.join(sorted(_REQUIRED))}"
            )
        accounts: list[Account] = []
        for row in reader:
            norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not norm.get("titan_email"):
                continue  # pula linhas em branco
            accounts.append(
                Account(
                    titan_email=norm["titan_email"],
                    titan_password=norm["titan_password"],
                    target_email=norm["target_email"],
                )
            )
    if not accounts:
        raise SystemExit("Nenhuma conta válida encontrada no CSV.")
    return accounts
