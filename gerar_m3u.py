#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gerador automático de M3U da Soul TV.

- Consulta a API de canais da Soul TV.
- Mantém apenas canais com URL de transmissão.
- Testa cada stream HLS antes de colocar no M3U.
- Organiza por categoria.
- Gera todos os arquivos de saída no diretório raiz do projeto.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent

API_URLS = [
    "https://cms.soultv.com.br/v1/brand?country=BR&language=pt&platform=web",
    "https://cms.soultv.com.br/v1/brand?country=US&language=pt&platform=web",
]

# Token público atualmente usado pelo cliente web da Soul TV.
# Pode ser substituído por SOULTV_TOKEN no GitHub Actions sem alterar o código.
DEFAULT_TOKEN = "40d74c5c81385acde170e37cbe45ae74d74f53ed"

OUTPUT_M3U = ROOT / "soultv.m3u"
OUTPUT_JSON = ROOT / "canais_soultv.json"
OUTPUT_STATUS = ROOT / "status_soultv.json"
OUTPUT_RAW = ROOT / "soultv_api.json"

TIMEOUT_API = 30
TIMEOUT_STREAM = 12
MAX_WORKERS = 16

CATEGORY_MAP = {
    "1": "Agronegócios",
    "2": "Cultura",
    "3": "Esportes / Games",
    "4": "Entretenimento",
    "5": "Gastronomia",
    "6": "Internacional",
    "8": "Minas",
    "9": "Música",
    "10": "Moda / Beleza",
    "11": "Religioso",
    "12": "Regionais",
    "13": "Shopping",
    "14": "Variedades",
    "15": "Viagens",
    "16": "Filmes / Séries",
    "17": "Pet",
    "18": "Infantil",
    "19": "Jornalismo",
}

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.soultv.com.br",
    "Referer": "https://www.soultv.com.br/",
    "language": "pt",
    "platform": "web",
})


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def token() -> str:
    return os.getenv("SOULTV_TOKEN", DEFAULT_TOKEN).strip()


def get_categories(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    result = []
    for item in raw:
        item = clean(item)
        if not item or item.upper() == "ALL":
            continue
        result.append(CATEGORY_MAP.get(item, item))
    return result


def normalize_channel(c: dict[str, Any]) -> dict[str, Any] | None:
    name = clean(c.get("name") or c.get("title"))
    url = clean(c.get("url_live_streaming") or c.get("url") or c.get("stream_url"))
    if not name or not url or not url.startswith(("http://", "https://")):
        return None

    logo = clean(c.get("image") or c.get("image2") or c.get("logo"))
    cid = clean(c.get("id") or c.get("channel_id") or name)
    categories = get_categories(c.get("category") or c.get("categories"))

    if not categories:
        categories = ["Variedades"]

    # Evita categorias estranhas/IDs como grupo quando a API trouxer algo inesperado.
    categories = [x for x in categories if x and not x.isdigit()]
    if not categories:
        categories = ["Variedades"]

    return {
        "id": cid,
        "name": name,
        "logo": logo,
        "url": url,
        "categories": categories,
    }


def extract_channels(payload: Any) -> list[dict[str, Any]]:
    candidates = []
    if isinstance(payload, dict):
        for key in ("data", "channels", "brands", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                for subkey in ("data", "items", "channels", "results"):
                    if isinstance(value.get(subkey), list):
                        candidates.extend(value[subkey])
        if not candidates:
            # Procura listas de objetos em qualquer nível imediato.
            for value in payload.values():
                if isinstance(value, list) and any(isinstance(x, dict) for x in value):
                    candidates.extend(value)
    elif isinstance(payload, list):
        candidates = payload

    normalized = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        ch = normalize_channel(item)
        if not ch:
            continue
        key = (ch["name"].casefold(), ch["url"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(ch)
    return normalized


def fetch_api() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    headers = {"Authorization": f"token {token()}"}
    errors = []

    for api_url in API_URLS:
        try:
            response = session.get(api_url, headers=headers, timeout=TIMEOUT_API)
            if response.status_code != 200:
                errors.append(f"{api_url} -> HTTP {response.status_code}")
                continue

            payload = response.json()
            channels = extract_channels(payload)
            if channels:
                return channels, {
                    "api_url": api_url,
                    "http_status": response.status_code,
                    "raw": payload,
                    "errors": errors,
                }

            errors.append(f"{api_url} -> resposta sem canais reconhecíveis")
        except Exception as exc:
            errors.append(f"{api_url} -> {type(exc).__name__}: {exc}")

    raise RuntimeError("Nenhum canal foi obtido da API. " + " | ".join(errors))


def looks_like_hls(text: str, content_type: str) -> bool:
    t = (text or "")[:20000]
    ct = (content_type or "").lower()
    return (
        "#extm3u" in t.lower()
        or "#ext-x-" in t.lower()
        or "mpegurl" in ct
        or "vnd.apple.mpegurl" in ct
    )


def test_stream(ch: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
    url = ch["url"]
    try:
        # GET parcial é mais compatível que HEAD com servidores HLS.
        r = requests.get(
            url,
            headers={
                "User-Agent": session.headers["User-Agent"],
                "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
                "Range": "bytes=0-4095",
            },
            timeout=TIMEOUT_STREAM,
            allow_redirects=True,
            stream=True,
        )
        content_type = r.headers.get("Content-Type", "")
        sample = b""
        try:
            sample = next(r.iter_content(chunk_size=4096), b"")
        finally:
            r.close()

        text = sample.decode("utf-8", errors="ignore")
        ok_status = 200 <= r.status_code < 400
        ok_hls = looks_like_hls(text, content_type)

        # Alguns servidores retornam 200 com playlist HLS; outros retornam 206.
        if ok_status and (ok_hls or ".m3u8" in url.lower()):
            return ch, True, f"HTTP {r.status_code}"
        return ch, False, f"HTTP {r.status_code}, conteúdo não reconhecido como HLS"
    except Exception as exc:
        return ch, False, f"{type(exc).__name__}: {exc}"


def build_m3u(active: list[dict[str, Any]]) -> str:
    lines = [
        "#EXTM3U",
        "#PLAYLIST:Soultv Brasil - atualizado automaticamente",
        "",
    ]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for ch in active:
        for category in ch["categories"]:
            grouped.setdefault(category, []).append(ch)

    # Um canal pertence à primeira categoria da API para evitar duplicação.
    unique: dict[str, dict[str, Any]] = {}
    for ch in active:
        category = ch["categories"][0]
        unique.setdefault(ch["url"], {**ch, "group": category})

    for ch in sorted(unique.values(), key=lambda x: (x["group"].casefold(), x["name"].casefold())):
        attrs = [
            f'tvg-id="{ch["id"]}"',
            f'tvg-name="{ch["name"].replace(chr(34), "")}"',
        ]
        if ch["logo"]:
            attrs.append(f'tvg-logo="{ch["logo"].replace(chr(34), "")}"')
        attrs.append(f'group-title="{ch["group"].replace(chr(34), "")}"')

        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{ch["name"]}')
        lines.append(ch["url"])
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    started = time.time()
    print("=== Soul TV M3U ===")
    print("Consultando API...")

    try:
        channels, meta = fetch_api()
    except Exception as exc:
        OUTPUT_STATUS.write_text(
            json.dumps({
                "ok": False,
                "erro": str(exc),
                "atualizado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"ERRO: {exc}")
        return 1

    OUTPUT_RAW.write_text(
        json.dumps(meta["raw"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Canais encontrados na API: {len(channels)}")
    print("Testando streams ativos...")

    active = []
    failures = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(test_stream, ch) for ch in channels]
        for future in concurrent.futures.as_completed(futures):
            ch, ok, reason = future.result()
            if ok:
                ch["status"] = "ativo"
                ch["teste"] = reason
                active.append(ch)
            else:
                failures.append({
                    "name": ch["name"],
                    "url": ch["url"],
                    "status": "inativo",
                    "motivo": reason,
                })

    active.sort(key=lambda x: (x["categories"][0].casefold(), x["name"].casefold()))

    OUTPUT_M3U.write_text(build_m3u(active), encoding="utf-8")

    OUTPUT_JSON.write_text(
        json.dumps(active, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    status = {
        "ok": True,
        "fonte": "https://www.soultv.com.br/",
        "api": meta["api_url"],
        "total_api": len(channels),
        "total_ativos": len(active),
        "total_removidos_teste": len(failures),
        "canais_ativos": [x["name"] for x in active],
        "falhas": failures,
        "atualizado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duracao_segundos": round(time.time() - started, 2),
    }

    OUTPUT_STATUS.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not active:
        print("ERRO: nenhum stream ativo foi aprovado.")
        return 2

    print(f"Streams ativos: {len(active)}")
    print(f"Streams removidos: {len(failures)}")
    print(f"M3U: {OUTPUT_M3U}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
