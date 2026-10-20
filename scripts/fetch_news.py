#!/usr/bin/env python3
"""Coleta manchetes via RSS, separadas por categoria, e monta o prompt do Hermes Agent.

Esta etapa NÃO usa IA. Ela é determinística de propósito:
- o agente não navega na web nem roda comandos; só recebe uma lista de títulos;
- só títulos e links entram no prompt (sem o corpo das matérias), o que reduz
  a superfície para prompt injection;
- tudo que vem dos feeds é tratado como dado não confiável e higienizado.

Só usa a biblioteca padrão do Python.

Uso:
    python scripts/fetch_news.py --feeds feeds.txt --template prompts/briefing.txt \
        --hours 24 --out prompt.txt
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

USER_AGENT = "hermes-briefing-diario/1.0"
TIMEOUT_SECONDS = 20
MAX_FEED_BYTES = 5 * 1024 * 1024   # não baixa feeds gigantes
MAX_ITEMS_PER_FEED = 30            # nenhum veículo domina a lista
MAX_TITLE_CHARS = 200
MIN_SOURCES_PER_CATEGORY = 2       # menos que isso, a seção sairia enviesada
TZ_BR = ZoneInfo("America/Sao_Paulo")

ATOM_NS = "{http://www.w3.org/2005/Atom}"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


def read_feeds(path: str) -> list[tuple[str, str, str]]:
    """Lê linhas 'Categoria | Nome | URL'."""
    feeds = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3 and all(parts) and parts[2].startswith("https://"):
                feeds.append((parts[0], parts[1], parts[2]))
            else:
                print(f"[ignorada] linha mal formatada em {path}: {line}", file=sys.stderr)
    return feeds


class HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Recusa redirecionamento para fora do HTTPS (ex.: um feed que mande para http://)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ValueError(f"redirecionamento recusado para endereço sem HTTPS: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HttpsOnlyRedirects)


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
        data = resp.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("feed maior que o limite")
    return data


def parse_date(text: str | None) -> datetime | None:
    """Aceita RFC 822 (padrão do RSS) e ISO 8601 (usado por alguns feeds)."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_BR)
    return dt


def clean(text: str | None) -> str:
    """Higieniza texto vindo da internet antes de ir para o prompt."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)       # remove tags HTML
    text = text.replace("<", "").replace(">", "")  # impede forjar os delimitadores <<< >>>
    text = text.replace("#", "")                 # impede forjar cabeçalhos de seção
    text = re.sub(r"\s+", " ", text).strip()     # uma linha só
    return text[:MAX_TITLE_CHARS]


def parse_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    # RSS 2.0
    for item in root.iter("item"):
        items.append({
            "title": item.findtext("title"),
            "link": item.findtext("link"),
            "date": item.findtext("pubDate") or item.findtext(DC_DATE),
        })
    # Atom (caso algum feed mude de formato)
    for entry in root.iter(f"{ATOM_NS}entry"):
        link_el = entry.find(f"{ATOM_NS}link")
        items.append({
            "title": entry.findtext(f"{ATOM_NS}title"),
            "link": link_el.get("href") if link_el is not None else None,
            "date": entry.findtext(f"{ATOM_NS}updated") or entry.findtext(f"{ATOM_NS}published"),
        })
    return items


def collect(feeds: list[tuple[str, str, str]], hours: int) -> dict[str, dict]:
    """Retorna {categoria: {"headlines": [...], "sources": [...]}} na ordem do arquivo."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result: dict[str, dict] = {}
    seen = set()

    for category, name, url in feeds:
        bucket = result.setdefault(category, {"headlines": [], "sources": []})
        try:
            items = parse_items(download(url))
        except Exception as exc:  # um feed fora do ar não derruba os outros
            print(f"[falhou] {category} / {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        kept = 0
        for it in items:
            title = clean(it["title"])
            link = (it["link"] or "").strip()
            if not title or not link.startswith("https://"):
                continue
            dt = parse_date(it["date"])
            if dt is not None and dt < cutoff:
                continue
            # Duplicata só dentro do MESMO veículo. O mesmo assunto em veículos
            # diferentes é justamente o sinal de "mais falado".
            key = (name, re.sub(r"\W+", "", title.lower()))
            if key in seen:
                continue
            seen.add(key)
            bucket["headlines"].append({"source": name, "title": title, "link": link})
            kept += 1
            if kept >= MAX_ITEMS_PER_FEED:
                break

        print(f"[ok] {category} / {name}: {kept} manchetes nas últimas {hours}h", file=sys.stderr)
        if kept:
            bucket["sources"].append(name)

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--feeds", default="feeds.txt")
    ap.add_argument("--template", default="prompts/briefing.txt")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--out", default="prompt.txt")
    args = ap.parse_args()

    collected = collect(read_feeds(args.feeds), args.hours)

    blocks, total_headlines, total_sources = [], 0, 0
    for category, data in collected.items():
        if len(data["sources"]) < MIN_SOURCES_PER_CATEGORY:
            print(f"[seção omitida] {category}: só {len(data['sources'])} fonte(s) responderam.",
                  file=sys.stderr)
            continue
        lines = "\n".join(f"- [{h['source']}] {h['title']} | {h['link']}"
                          for h in data["headlines"])
        blocks.append(f"## {category}\n{lines}")
        total_headlines += len(data["headlines"])
        total_sources += len(data["sources"])

    if not blocks:
        print("Nenhuma seção com fontes suficientes; abortando.", file=sys.stderr)
        return 1

    with open(args.template, encoding="utf-8") as f:
        template = f.read()

    prompt = (template
              .replace("{DATA}", datetime.now(TZ_BR).strftime("%d/%m/%Y"))
              .replace("{HORAS}", str(args.hours))
              .replace("{N_FONTES}", str(total_sources))
              .replace("{MANCHETES}", "\n\n".join(blocks)))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(prompt)

    print(f"Total: {total_headlines} manchetes de {total_sources} fontes em "
          f"{len(blocks)} seção(ões) -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
