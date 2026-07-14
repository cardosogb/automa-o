"""Obtenção de token OAuth2 (client credentials / app-only) para o Microsoft 365.

Usa o fluxo *client credentials* do Microsoft Entra ID (Azure AD). Com ele o
script consegue autenticar em qualquer caixa do tenant sem login interativo,
o que é essencial para migrar muitas contas em lote.

Pré-requisitos no Azure/Entra (feitos uma única vez pelo admin):
  1. Registrar um aplicativo (App registration).
  2. Conceder a permissão de API *Application* `IMAP.AccessAsApp`
     (Office 365 Exchange Online) e dar consentimento de admin.
  3. Registrar o service principal no Exchange Online e liberar o acesso às
     caixas (New-ServicePrincipal / Add-MailboxPermission). Passo a passo no
     README.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

# Escopo fixo para acesso IMAP/POP app-only no Exchange Online.
_SCOPE = "https://outlook.office365.com/.default"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class TokenProvider:
    """Busca e faz cache de um token de acesso app-only, renovando quando expira."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get_token(self) -> str:
        # Renova com 60s de folga antes da expiração.
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        self._token, expires_in = self._request_token()
        self._expires_at = time.time() + expires_in
        return self._token

    def _request_token(self) -> tuple[str, int]:
        url = _TOKEN_URL.format(tenant=self._tenant_id)
        data = urllib.parse.urlencode(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": _SCOPE,
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Falha ao obter token OAuth2 ({exc.code}): {detail}"
            ) from exc
        return payload["access_token"], int(payload.get("expires_in", 3600))


def build_xoauth2(user: str, access_token: str) -> bytes:
    """Monta a string de autenticação SASL XOAUTH2 usada pelo IMAP."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8"))
