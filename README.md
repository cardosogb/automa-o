# automail — Backup e migração de contas Titan → Microsoft 365 (IMAP)

Ferramenta de linha de comando que:

1. **Faz backup** de cada conta **Titan** (via IMAP) para uma pasta local —
   cada backup fica em uma pasta com o **nome do e-mail antes do `@`**
   (ex.: `joao@empresa.com` → `backups/joao/`), preservando toda a estrutura
   de pastas e as flags das mensagens.
2. **Migra** (vincula) todo o conteúdo para a **conta nova do Microsoft 365**
   (pacote Office), via IMAP com **OAuth2 app-only**, recriando as pastas e
   mapeando as pastas especiais (Enviados, Rascunhos, Lixeira, Spam, Arquivo)
   para os nomes corretos do M365.

> **Importante:** todo o processo roda **direto entre os servidores IMAP**.
> Não é necessário ter o Outlook Classic aberto nem instalado — o Outlook é
> apenas onde as contas estão configuradas hoje. Você roda este script na sua
> máquina (Windows, macOS ou Linux); ele não depende do Outlook.

---

## Requisitos

- **Python 3.10+** (usa só a biblioteca padrão — sem dependências externas).
- Acesso IMAP habilitado nas contas Titan (senha ou senha de aplicativo).
- Um **App Registration** no Microsoft Entra ID com permissão IMAP app-only
  (passo a passo abaixo). Isso normalmente exige um **administrador** do tenant.

---

## Instalação

```bash
git clone <este-repositório>
cd automa-o
cp .env.example .env
cp accounts.example.csv accounts.csv
```

Edite `.env` e `accounts.csv` (ambos ficam fora do Git por segurança).

### `accounts.csv`

Uma linha por conta. Colunas obrigatórias:

| Coluna           | Descrição                                             |
|------------------|-------------------------------------------------------|
| `titan_email`    | E-mail de origem no Titan                             |
| `titan_password` | Senha (ou senha de app) do Titan                      |
| `target_email`   | E-mail de destino no Microsoft 365                    |

> O destino usa OAuth2 app-only, então **não** é preciso a senha da conta M365.

---

## Configurar o OAuth2 no Azure (uma vez)

Feito por um administrador do tenant Microsoft 365:

1. **Registrar o app**: Entra ID → *App registrations* → *New registration*.
   Anote o **Directory (tenant) ID** e o **Application (client) ID**.
2. **Criar um segredo**: *Certificates & secrets* → *New client secret*.
   Copie o **Value** (é o `AZURE_CLIENT_SECRET`).
3. **Permissão de API**: *API permissions* → *Add a permission* →
   *APIs my organization uses* → **Office 365 Exchange Online** →
   *Application permissions* → marque **`IMAP.AccessAsApp`** →
   *Add permissions* → **Grant admin consent**.
4. **Registrar o service principal no Exchange e liberar as caixas**
   (Exchange Online PowerShell):

   ```powershell
   # Registra o app no Exchange
   New-ServicePrincipal -AppId <CLIENT_ID> -ObjectId <ENTERPRISE_APP_OBJECT_ID>

   # Dá ao app acesso total a cada caixa de destino
   Add-MailboxPermission -Identity "joao@empresa.onmicrosoft.com" `
       -User <SERVICE_PRINCIPAL_ID> -AccessRights FullAccess
   ```

   Referência oficial: *"Authenticate an IMAP, POP or SMTP connection using
   OAuth"* na documentação da Microsoft.
5. Preencha `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` e `AZURE_CLIENT_SECRET` no `.env`.

---

## Uso

```bash
# Fluxo completo: backup + migração
python -m automail run

# Só backup (baixar do Titan para o disco)
python -m automail backup

# Só migração (subir backups já baixados para o M365)
python -m automail migrate

# Processar apenas uma conta específica
python -m automail run --only joao@empresa.com

# Usar um CSV diferente
python -m automail run --csv outras_contas.csv
```

### Como funciona a idempotência

- **Backup**: mensagens já salvas (mesmo UID) são puladas — pode rodar de novo
  sem baixar tudo outra vez.
- **Migração**: antes de subir, verifica o `Message-ID` no destino; se já
  existe, não duplica. Assim você pode reexecutar com segurança.

---

## Estrutura do backup gerado

```
backups/
└── joao/                     # nome = e-mail antes do @
    ├── manifest.json         # índice das pastas e contagens
    ├── INBOX/
    │   ├── 12.eml
    │   └── 12.meta.json      # flags + data original da mensagem
    └── Sent/
        └── ...
```

---

## Segurança

- `.env`, `accounts.csv` e a pasta `backups/` estão no `.gitignore` — não são
  versionados, pois contêm credenciais e dados pessoais.
- Prefira **senhas de aplicativo** quando o provedor oferecer.
- Rode em uma máquina confiável; os backups `.eml` contêm o conteúdo integral
  dos e-mails.
```
