#!/usr/bin/env python3
"""Confere e envia o briefing gerado pelo Hermes por email (Gmail, via SMTP com TLS).

Credenciais vêm de variáveis de ambiente (no GitHub, dos Secrets):
    GMAIL_ADDRESS       conta Gmail que envia o briefing
    GMAIL_APP_PASSWORD  senha de app dessa conta (nunca a senha normal)
    EMAIL_TO            destinatário

Uso:
    python scripts/send_email.py digest.txt --allowed-links prompt.txt
    python scripts/send_email.py digest.txt --allowed-links prompt.txt --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SMTP sobre TLS desde o início da conexão
URL_RE = re.compile(r"https?://[^\s<>\"'|]+")
REMOVED = "[link removido: não veio das fontes coletadas]"


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def enforce_allowed_links(body: str, allowed_file: str) -> tuple[str, int]:
    """Troca qualquer link que não esteja na lista coletada.

    A IA lê texto da internet e poderia ser induzida a incluir um link de golpe.
    Essa garantia fica no código: só sai no email link que a coleta encontrou.
    """
    with open(allowed_file, encoding="utf-8") as f:
        allowed = {clean_url(u) for u in URL_RE.findall(f.read()) if u.startswith("https://")}

    removed = 0

    def check(match: re.Match) -> str:
        nonlocal removed
        url = clean_url(match.group(0))
        if url in allowed:
            return match.group(0)
        removed += 1
        return REMOVED + match.group(0)[len(url):]  # mantém a pontuação que vinha depois

    return URL_RE.sub(check, body), removed


def main() -> int:
    ap = argparse.ArgumentParser(description="Confere e envia o briefing por email.")
    ap.add_argument("digest", help="arquivo de texto com o briefing")
    ap.add_argument("--allowed-links", required=True,
                    help="arquivo com as manchetes coletadas (os únicos links permitidos)")
    ap.add_argument("--dry-run", action="store_true", help="não envia, só mostra")
    args = ap.parse_args()

    with open(args.digest, encoding="utf-8") as f:
        body = f.read().strip()

    if len(body) < 50:
        print("Briefing vazio ou curto demais; nada foi enviado.", file=sys.stderr)
        return 1

    body, removed = enforce_allowed_links(body, args.allowed_links)
    if removed:
        print(f"ATENÇÃO: {removed} link(s) fora da lista coletada foram removidos.",
              file=sys.stderr)

    # Aviso obrigatório garantido pelo código, não pelo modelo:
    # o que precisa sair sempre não deve depender da IA lembrar.
    if "CRIPTO" in body and "recomendação de investimento" not in body:
        body += "\nCripto: informativo, não é recomendação de investimento."

    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")
    sender = os.environ.get("GMAIL_ADDRESS", "remetente@example.com")
    recipient = os.environ.get("EMAIL_TO", "destinatario@example.com")

    msg = EmailMessage()
    msg["Subject"] = f"Briefing — {today}"
    msg["From"] = f"Briefing Diário <{sender}>"
    msg["To"] = recipient
    msg.set_content(body)  # texto simples: minimalista e sem rastreadores

    if args.dry_run:
        print(body)
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
