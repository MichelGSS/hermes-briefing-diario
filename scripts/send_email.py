#!/usr/bin/env python3
"""Envia o briefing gerado pelo Hermes por email (Gmail, via SMTP com TLS).

Credenciais vêm de variáveis de ambiente (no GitHub, dos Secrets):
    GMAIL_ADDRESS       conta Gmail dedicada que envia o briefing
    GMAIL_APP_PASSWORD  senha de app dessa conta (nunca a senha normal)
    EMAIL_TO            destinatário (o seu email)

Uso:
    python scripts/send_email.py digest.txt
    python scripts/send_email.py digest.txt --dry-run   # mostra o email sem enviar
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SMTP sobre TLS desde o início da conexão


def main() -> int:
    ap = argparse.ArgumentParser(description="Envia o briefing por email.")
    ap.add_argument("digest", help="arquivo de texto com o briefing")
    ap.add_argument("--dry-run", action="store_true", help="não envia, só mostra")
    args = ap.parse_args()

    with open(args.digest, encoding="utf-8") as f:
        body = f.read().strip()

    if len(body) < 50:
        print("Briefing vazio ou curto demais; nada foi enviado.", file=sys.stderr)
        return 1

    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")
    sender = os.environ.get("GMAIL_ADDRESS", "remetente@example.com")
    recipient = os.environ.get("EMAIL_TO", "destinatario@example.com")

    msg = EmailMessage()
    msg["Subject"] = f"Briefing — {today}"
    msg["From"] = f"Briefing Diário <{sender}>"
    msg["To"] = recipient
    msg.set_content(body)  # texto simples: minimalista e sem rastreadores

    if args.dry_run:
        print(msg)
        return 0

    missing = [v for v in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "EMAIL_TO")
               if not os.environ.get(v)]
    if missing:
        print(f"Faltam variáveis de ambiente: {', '.join(missing)}", file=sys.stderr)
        return 1

    context = ssl.create_default_context()  # valida o certificado do servidor
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as smtp:
        smtp.login(sender, os.environ["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)

    print("Briefing enviado.")  # não imprime o destinatário: o log do Actions é público
    return 0


if __name__ == "__main__":
    sys.exit(main())
