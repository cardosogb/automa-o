"""Orquestração: backup do Titan e migração para o Microsoft 365."""

from __future__ import annotations

import email
import json
import mailbox
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from .accounts import Account
from .config import Settings
from .imap_client import Folder, ImapConnection, with_retry
from .oauth import TokenProvider, build_xoauth2

# Mapa de special-use -> nome canônico no Microsoft 365.
_M365_SPECIAL_NAMES = {
    "\\sent": "Sent Items",
    "\\drafts": "Drafts",
    "\\junk": "Junk Email",
    "\\trash": "Deleted Items",
    "\\archive": "Archive",
}


def _safe_component(name: str) -> str:
    """Torna um nome de pasta seguro para virar diretório no disco."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "_"


# ----------------------------------------------------------------------------
# BACKUP
# ----------------------------------------------------------------------------
def backup_account(account: Account, settings: Settings) -> Path:
    """Baixa todas as pastas/mensagens da conta Titan para o disco.

    Layout gerado:
        backups/<prefixo>/
            manifest.json
            <Pasta>/
                <uid>.eml
                <uid>.meta.json
    """
    dest = Path(settings.backup_dir) / account.local_prefix
    dest.mkdir(parents=True, exist_ok=True)
    print(f"\n=== BACKUP {account.titan_email} -> {dest} ===")

    src = ImapConnection(settings.titan_host, settings.titan_port)
    try:
        with_retry(
            lambda: src.login_basic(account.titan_email, account.titan_password),
            settings.max_retries,
            "login Titan",
        )
        folders = [f for f in src.list_folders() if f.is_selectable]
        manifest: dict = {
            "titan_email": account.titan_email,
            "target_email": account.target_email,
            "folders": [],
        }

        for folder in folders:
            count = with_retry(
                lambda f=folder: src.select(f.name, readonly=True),
                settings.max_retries,
                f"select {folder.name}",
            )
            uids = src.search_all_uids()
            print(f"  • {folder.name}: {len(uids)} mensagem(ns)")
            folder_dir = dest / _safe_component(folder.name)
            folder_dir.mkdir(parents=True, exist_ok=True)

            saved = 0
            for uid in uids:
                uid_s = uid.decode()
                eml_path = folder_dir / f"{uid_s}.eml"
                meta_path = folder_dir / f"{uid_s}.meta.json"
                if eml_path.exists() and meta_path.exists():
                    saved += 1
                    continue  # idempotente: já baixado
                raw, flags, internaldate = with_retry(
                    lambda u=uid: src.fetch_message(u),
                    settings.max_retries,
                    f"fetch {folder.name}#{uid_s}",
                )
                eml_path.write_bytes(raw)
                meta_path.write_text(
                    json.dumps(
                        {"uid": uid_s, "flags": flags, "internaldate": internaldate},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                saved += 1

            manifest["folders"].append(
                {
                    "name": folder.name,
                    "dir": _safe_component(folder.name),
                    "special_use": folder.special_use,
                    "count": saved,
                }
            )

        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        total = sum(f["count"] for f in manifest["folders"])
        print(f"  ✓ backup concluído: {total} mensagem(ns) em {len(folders)} pasta(s)")
    finally:
        src.logout()
    return dest


# ----------------------------------------------------------------------------
# MIGRAÇÃO
# ----------------------------------------------------------------------------
def _target_folder_name(
    special_use: str | None,
    source_name: str,
    target_folders: list[Folder],
    target_conn: ImapConnection,
) -> str:
    """Decide em qual pasta do M365 a mensagem deve entrar."""
    if special_use:
        existing = target_conn.find_special(target_folders, special_use)
        if existing:
            return existing
        return _M365_SPECIAL_NAMES.get(special_use, source_name)
    # INBOX é sempre INBOX.
    if source_name.upper() == "INBOX":
        return "INBOX"
    return source_name


def migrate_account(
    account: Account,
    settings: Settings,
    token_provider: TokenProvider,
) -> None:
    """Sobe o backup local da conta para a caixa nova do Microsoft 365."""
    dest = Path(settings.backup_dir) / account.local_prefix
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        print(f"  ! sem backup para {account.titan_email}; rode o backup antes.")
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"\n=== MIGRAÇÃO {account.titan_email} -> {account.target_email} ===")

    token = token_provider.get_token()
    tgt = ImapConnection(settings.m365_host, settings.m365_port)
    try:
        with_retry(
            lambda: tgt.login_xoauth2(
                account.target_email,
                build_xoauth2(account.target_email, token),
            ),
            settings.max_retries,
            "login M365",
        )
        target_folders = tgt.list_folders()

        for finfo in manifest["folders"]:
            source_name = finfo["name"]
            folder_dir = dest / finfo["dir"]
            if not folder_dir.is_dir():
                continue

            target_name = _target_folder_name(
                finfo.get("special_use"), source_name, target_folders, tgt
            )
            if target_name.upper() != "INBOX":
                tgt.create_folder(target_name)
                # Atualiza a lista após criar, para dedup por special-use.
                target_folders = tgt.list_folders()

            tgt.select(target_name, readonly=False)
            eml_files = sorted(folder_dir.glob("*.eml"))
            print(f"  • {source_name} -> {target_name}: {len(eml_files)} mensagem(ns)")

            uploaded = 0
            skipped = 0
            for eml_path in eml_files:
                meta_path = eml_path.with_suffix(".meta.json")
                flags: list[str] = []
                internaldate = None
                if meta_path.is_file():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    flags = meta.get("flags", [])
                    internaldate = meta.get("internaldate")

                raw = eml_path.read_bytes()
                msg_id = _extract_message_id(raw)

                # Idempotência: não duplica se o Message-ID já existe no destino.
                if msg_id and tgt.search_message_id(msg_id):
                    skipped += 1
                    continue

                if internaldate is None:
                    internaldate = _fallback_internaldate(raw)

                with_retry(
                    lambda r=raw, fl=flags, dt=internaldate, tn=target_name: (
                        tgt.append_message(tn, r, fl, dt)
                    ),
                    settings.max_retries,
                    f"append {target_name}/{eml_path.name}",
                )
                uploaded += 1
                if settings.upload_throttle:
                    time.sleep(settings.upload_throttle)

            print(f"    ✓ enviadas {uploaded}, já existentes {skipped}")
        print(f"  ✓ migração concluída para {account.target_email}")
    finally:
        tgt.logout()


def _extract_message_id(raw: bytes) -> str | None:
    try:
        msg = email.message_from_bytes(raw)
        mid = msg.get("Message-ID") or msg.get("Message-Id")
        return mid.strip() if mid else None
    except Exception:
        return None


def _fallback_internaldate(raw: bytes) -> str | None:
    """Usa o header Date se o INTERNALDATE não foi capturado."""
    try:
        msg = email.message_from_bytes(raw)
        date_hdr = msg.get("Date")
        if not date_hdr:
            return None
        import imaplib

        dt = parsedate_to_datetime(date_hdr)
        return imaplib.Time2Internaldate(dt.timestamp()).strip('"')
    except Exception:
        return None


# ----------------------------------------------------------------------------
# EXPORTAR PARA MBOX (importável em Thunderbird / Outlook)
# ----------------------------------------------------------------------------
def export_mbox(account: Account, settings: Settings) -> Path | None:
    """Converte o backup .eml da conta em arquivos .mbox (um por pasta).

    Saída:
        backups/<prefixo>/_mbox/<NomeDaPasta>.mbox
    Cada .mbox pode ser importado direto no Thunderbird (addon ImportExportTools
    NG) ou copiado para a pasta de perfil do cliente de e-mail.
    """
    dest = Path(settings.backup_dir) / account.local_prefix
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        print(f"  ! sem backup para {account.titan_email}; rode o backup antes.")
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out_dir = dest / "_mbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== EXPORTAR MBOX {account.titan_email} -> {out_dir} ===")

    total = 0
    for finfo in manifest["folders"]:
        folder_dir = dest / finfo["dir"]
        if not folder_dir.is_dir():
            continue
        eml_files = sorted(folder_dir.glob("*.eml"))
        if not eml_files:
            continue

        mbox_path = out_dir / f"{_safe_component(finfo['name'])}.mbox"
        # Recria do zero para ser idempotente.
        if mbox_path.exists():
            mbox_path.unlink()
        mb = mailbox.mbox(str(mbox_path))
        mb.lock()
        try:
            for eml_path in eml_files:
                raw = eml_path.read_bytes()
                msg = mailbox.mboxMessage(raw)
                # Preserva flag de lida a partir do .meta.json, se houver.
                meta_path = eml_path.with_suffix(".meta.json")
                if meta_path.is_file():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if "\\Seen" in meta.get("flags", []):
                        msg.add_flag("R")
                mb.add(msg)
            mb.flush()
        finally:
            mb.unlock()
            mb.close()
        print(f"  • {finfo['name']} -> {mbox_path.name} ({len(eml_files)} msg)")
        total += len(eml_files)

    print(f"  ✓ {total} mensagem(ns) exportada(s) em {out_dir}")
    return out_dir


# ----------------------------------------------------------------------------
# DIAGNÓSTICO (check)
# ----------------------------------------------------------------------------
def check_account(
    account: Account,
    settings: Settings,
    token_provider: TokenProvider,
) -> bool:
    """Testa origem (Titan), token OAuth e destino (M365) sem mover e-mails.

    Retorna True se todos os testes passarem.
    """
    print(f"\n=== CHECK {account.titan_email} -> {account.target_email} ===")
    ok = True

    # 1) Login IMAP no Titan (origem)
    try:
        src = ImapConnection(settings.titan_host, settings.titan_port)
        src.login_basic(account.titan_email, account.titan_password)
        folders = [f for f in src.list_folders() if f.is_selectable]
        src.logout()
        print(f"  [OK] Titan: login IMAP e {len(folders)} pasta(s) visível(is)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FALHA] Titan: {exc}")

    # 2) Token OAuth2 (Azure)
    token = None
    try:
        token = token_provider.get_token()
        print("  [OK] Azure: token OAuth2 obtido")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FALHA] Azure/OAuth2: {exc}")

    # 3) Login IMAP no M365 (destino) via XOAUTH2
    if token:
        try:
            tgt = ImapConnection(settings.m365_host, settings.m365_port)
            tgt.login_xoauth2(
                account.target_email, build_xoauth2(account.target_email, token)
            )
            tfolders = tgt.list_folders()
            tgt.logout()
            print(
                f"  [OK] M365: login IMAP (XOAUTH2) e "
                f"{len(tfolders)} pasta(s) visível(is)"
            )
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FALHA] M365: {exc}")
            print(
                "         Dicas: confira a permissão FullAccess do service "
                "principal na caixa,\n"
                "         se o IMAP está habilitado (Set-CASMailbox -ImapEnabled "
                "$true) e o consentimento de admin da IMAP.AccessAsApp."
            )

    print(f"  => {'TUDO OK' if ok else 'HÁ FALHAS — ver acima'}")
    return ok
