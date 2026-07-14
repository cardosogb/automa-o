"""Carregamento de configuração (variáveis de ambiente + arquivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Carrega um arquivo .env simples para o ambiente (sem dependências externas).

    Só define a variável se ela ainda não estiver no ambiente, para que
    variáveis já exportadas no shell tenham prioridade.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Settings:
    """Parâmetros globais da execução."""

    # --- Origem (Titan) ---
    titan_host: str = "imap.titan.email"
    titan_port: int = 993

    # --- Destino (Microsoft 365) ---
    m365_host: str = "outlook.office365.com"
    m365_port: int = 993

    # --- OAuth2 app-only (client credentials) para o M365 ---
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""

    # --- Caminhos / comportamento ---
    accounts_csv: str = "accounts.csv"
    backup_dir: str = "backups"
    # Pausa (segundos) entre mensagens ao subir para o M365, para evitar throttling.
    upload_throttle: float = 0.1
    # Nº de tentativas em operações IMAP que falham por erro temporário.
    max_retries: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        def _get(name: str, default: str) -> str:
            return os.environ.get(name, default)

        return cls(
            titan_host=_get("TITAN_HOST", "imap.titan.email"),
            titan_port=int(_get("TITAN_PORT", "993")),
            m365_host=_get("M365_HOST", "outlook.office365.com"),
            m365_port=int(_get("M365_PORT", "993")),
            azure_tenant_id=_get("AZURE_TENANT_ID", ""),
            azure_client_id=_get("AZURE_CLIENT_ID", ""),
            azure_client_secret=_get("AZURE_CLIENT_SECRET", ""),
            accounts_csv=_get("ACCOUNTS_CSV", "accounts.csv"),
            backup_dir=_get("BACKUP_DIR", "backups"),
            upload_throttle=float(_get("UPLOAD_THROTTLE", "0.1")),
            max_retries=int(_get("MAX_RETRIES", "4")),
        )

    def require_oauth(self) -> None:
        """Valida que as credenciais do Azure estão presentes para a migração."""
        missing = [
            name
            for name, value in (
                ("AZURE_TENANT_ID", self.azure_tenant_id),
                ("AZURE_CLIENT_ID", self.azure_client_id),
                ("AZURE_CLIENT_SECRET", self.azure_client_secret),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "Credenciais OAuth2 ausentes: "
                + ", ".join(missing)
                + ".\nPreencha o arquivo .env (veja .env.example) antes de migrar."
            )
