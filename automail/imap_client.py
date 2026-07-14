"""Wrapper em cima de imaplib com utilidades para backup e migração."""

from __future__ import annotations

import imaplib
import re
import time
from dataclasses import dataclass

# imaplib limita o tamanho de uma linha de resposta; mensagens grandes estouram
# o default. Aumentamos para 10 MB.
imaplib._MAXLINE = 10_000_000  # type: ignore[attr-defined]

_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) "?(?P<delim>[^" ]*)"? (?P<name>.+)')


@dataclass
class Folder:
    """Uma pasta IMAP e seus atributos."""

    name: str  # nome cru, como retornado pelo servidor (pode vir entre aspas)
    flags: list[str]
    delimiter: str

    @property
    def special_use(self) -> str | None:
        """Retorna o atributo SPECIAL-USE normalizado (\\Sent, \\Trash, ...)."""
        for flag in self.flags:
            low = flag.lower()
            for known in (
                "\\sent",
                "\\drafts",
                "\\junk",
                "\\trash",
                "\\archive",
                "\\all",
                "\\flagged",
            ):
                if low == known:
                    return known
        return None

    @property
    def is_selectable(self) -> bool:
        return "\\noselect" not in (f.lower() for f in self.flags)


def _decode_mailbox(raw: bytes) -> str:
    """Decodifica um nome de mailbox de UTF-7 modificado (RFC 3501) para str."""
    name = raw.decode("ascii", "replace")
    name = name.strip().strip('"')
    try:
        return name.encode("ascii").decode("imap4-utf-7")  # type: ignore[arg-type]
    except (UnicodeDecodeError, LookupError):
        # imap4-utf-7 não é codec nativo; fallback abaixo.
        return _imap_utf7_decode(name)


def _imap_utf7_decode(text: str) -> str:
    """Decodificador mínimo de IMAP modified UTF-7 (para nomes de pasta acentuados)."""
    import base64

    res: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "&":
            end = text.find("-", i)
            if end == -1:
                res.append(ch)
                break
            chunk = text[i + 1 : end]
            if chunk == "":
                res.append("&")
            else:
                b64 = chunk.replace(",", "/")
                pad = "=" * ((4 - len(b64) % 4) % 4)
                decoded = base64.b64decode(b64 + pad)
                res.append(decoded.decode("utf-16-be"))
            i = end + 1
        else:
            res.append(ch)
            i += 1
    return "".join(res)


def _encode_mailbox(name: str) -> str:
    """Codifica um nome de pasta para IMAP modified UTF-7 (para criar/selecionar)."""
    import base64

    res: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "&":
            res.append("&-")
            i += 1
        elif 0x20 <= ord(ch) <= 0x7E:
            res.append(ch)
            i += 1
        else:
            j = i
            while j < len(name) and not (0x20 <= ord(name[j]) <= 0x7E):
                j += 1
            chunk = name[i:j].encode("utf-16-be")
            b64 = base64.b64encode(chunk).decode("ascii").rstrip("=").replace("/", ",")
            res.append("&" + b64 + "-")
            i = j
    return "".join(res)


class ImapConnection:
    """Conexão IMAP com helpers de listagem, fetch e append."""

    def __init__(self, host: str, port: int = 993) -> None:
        self._host = host
        self._port = port
        self.conn = imaplib.IMAP4_SSL(host, port)

    # --- Autenticação ---
    def login_basic(self, user: str, password: str) -> None:
        self.conn.login(user, password)

    def login_xoauth2(self, user: str, xoauth2_b64: bytes) -> None:
        self.conn.authenticate("XOAUTH2", lambda _: xoauth2_b64)

    # --- Pastas ---
    def list_folders(self) -> list[Folder]:
        typ, data = self.conn.list()
        if typ != "OK":
            raise RuntimeError(f"LIST falhou: {typ}")
        folders: list[Folder] = []
        for raw in data:
            if raw is None:
                continue
            if isinstance(raw, tuple):
                raw = raw[0]
            m = _LIST_RE.match(raw)
            if not m:
                continue
            flags = m.group("flags").decode("ascii", "replace").split()
            delim = m.group("delim").decode("ascii", "replace")
            name = _decode_mailbox(m.group("name"))
            folders.append(Folder(name=name, flags=flags, delimiter=delim or "/"))
        return folders

    def select(self, folder_name: str, readonly: bool = True) -> int:
        """Seleciona a pasta e retorna a quantidade de mensagens."""
        mailbox = '"' + _encode_mailbox(folder_name) + '"'
        typ, data = self.conn.select(mailbox, readonly=readonly)
        if typ != "OK":
            raise RuntimeError(f"SELECT {folder_name!r} falhou: {data!r}")
        return int(data[0]) if data and data[0] else 0

    def create_folder(self, folder_name: str) -> None:
        """Cria a pasta (ignora erro se já existir)."""
        mailbox = '"' + _encode_mailbox(folder_name) + '"'
        typ, data = self.conn.create(mailbox)
        # Alguns servidores retornam NO se já existe; tratamos como sucesso.
        if typ != "OK" and b"exist" not in (data[0] or b"").lower():
            # Não é fatal — só logamos via exceção controlada no chamador.
            pass

    def find_special(self, folders: list[Folder], attr: str) -> str | None:
        for f in folders:
            if f.special_use == attr.lower():
                return f.name
        return None

    # --- Mensagens ---
    def search_all_uids(self) -> list[bytes]:
        typ, data = self.conn.uid("SEARCH", None, "ALL")
        if typ != "OK":
            raise RuntimeError(f"SEARCH falhou: {typ}")
        if not data or not data[0]:
            return []
        return data[0].split()

    def fetch_message(self, uid: bytes) -> tuple[bytes, list[str], str | None]:
        """Retorna (rfc822_bytes, flags, internaldate) de uma mensagem por UID."""
        typ, data = self.conn.uid(
            "FETCH", uid, "(RFC822 FLAGS INTERNALDATE)"
        )
        if typ != "OK" or not data or data[0] is None:
            raise RuntimeError(f"FETCH uid={uid!r} falhou: {typ}")
        header = b""
        body = b""
        for part in data:
            if isinstance(part, tuple):
                header += part[0]
                body = part[1]
            elif isinstance(part, bytes):
                header += part
        flags = [f.decode() for f in imaplib.ParseFlags(header)]
        internaldate = None
        m = re.search(rb'INTERNALDATE "([^"]+)"', header)
        if m:
            internaldate = m.group(1).decode()
        return body, flags, internaldate

    def append_message(
        self,
        folder_name: str,
        raw: bytes,
        flags: list[str] | None = None,
        internaldate: str | None = None,
    ) -> None:
        mailbox = '"' + _encode_mailbox(folder_name) + '"'
        # Remove flags que o servidor de destino pode rejeitar em APPEND.
        clean_flags = [f for f in (flags or []) if f.lower() != "\\recent"]
        flag_str = "(" + " ".join(clean_flags) + ")" if clean_flags else None
        date_time = None
        if internaldate:
            try:
                date_time = imaplib.Time2Internaldate(
                    imaplib.Internaldate2tuple(
                        b'INTERNALDATE "' + internaldate.encode() + b'"'
                    )
                )
            except Exception:
                date_time = None
        typ, data = self.conn.append(mailbox, flag_str, date_time, raw)
        if typ != "OK":
            raise RuntimeError(f"APPEND em {folder_name!r} falhou: {data!r}")

    def search_message_id(self, message_id: str) -> bool:
        """Verifica se já existe mensagem com este Message-ID na pasta selecionada."""
        safe = message_id.replace('"', "")
        typ, data = self.conn.uid("SEARCH", None, "HEADER", "Message-ID", safe)
        return typ == "OK" and bool(data and data[0])

    def logout(self) -> None:
        try:
            self.conn.logout()
        except Exception:
            pass


def with_retry(fn, retries: int, label: str, delay: float = 2.0):
    """Executa fn() com backoff exponencial em erros temporários."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, RuntimeError) as exc:
            last = exc
            wait = delay * (2**attempt)
            print(f"  ! {label}: tentativa {attempt + 1}/{retries} falhou "
                  f"({exc}); aguardando {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"{label}: esgotou tentativas") from last
