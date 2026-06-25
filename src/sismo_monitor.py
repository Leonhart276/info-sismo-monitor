#!/usr/bin/env python3
"""Hourly earthquake/news monitor for Venezuela -> Discord webhook.

Designed for GitHub Actions. It reads public sources, builds a short digest,
sends it to Discord only when new items are detected, and updates state/state.json.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

STATE_FILE = Path(os.getenv("STATE_FILE", "state/state.json"))
DIGEST_TZ = ZoneInfo(os.getenv("DIGEST_TZ", "America/Argentina/Buenos_Aires"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "2.5"))
MAX_NEWS = int(os.getenv("MAX_NEWS", "8"))
MAX_QUAKES = int(os.getenv("MAX_QUAKES", "8"))
SEND_EMPTY_DIGEST = os.getenv("SEND_EMPTY_DIGEST", "false").lower() == "true"
FORCE_SEND = os.getenv("FORCE_SEND", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Broad bounding box around Venezuela and nearby offshore areas.
MIN_LAT = float(os.getenv("MIN_LAT", "0.0"))
MAX_LAT = float(os.getenv("MAX_LAT", "13.8"))
MIN_LON = float(os.getenv("MIN_LON", "-74.8"))
MAX_LON = float(os.getenv("MAX_LON", "-58.8"))

USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "sismo-venezuela-discord-monitor/1.0 (+https://github.com/)",
)

EMOJI_ALERT = "\U0001F6A8"
EMOJI_RED = "\U0001F534"
EMOJI_YELLOW = "\U0001F7E1"
EMOJI_GREEN = "\U0001F7E2"
EMOJI_GLOBE = "\U0001F30E"
EMOJI_NEWS = "\U0001F4F0"
EMOJI_WARN = "\u26A0\uFE0F"

RELEVANCE_RE = re.compile(
    r"\b(venezuela|venezolano|venezolana|caracas|la guaira|yaracuy|falcon|lara|carabobo|miranda|aragua|funvisis)\b",
    re.IGNORECASE,
)
SEISMIC_RE = re.compile(
    r"\b(sismo|sismos|terremoto|terremotos|temblor|temblores|replica|replicas|seismo|seismos|earthquake|quake)\b",
    re.IGNORECASE,
)
URGENT_RE = re.compile(
    r"\b(muerto|muertos|fallecido|fallecidos|herido|heridos|colapso|derrumb|danos|emergencia|alerta|aeropuerto|refugio|evacuad|desaparecid|victima|victimas)\b",
    re.IGNORECASE,
)

DEFAULT_NEWS_QUERIES = [
    "(sismo OR terremoto OR temblor OR replicas OR replica) Venezuela when:1d",
    "FUNVISIS sismo Venezuela when:1d",
    "Proteccion Civil Venezuela sismo when:1d",
]


@dataclass(frozen=True)
class Item:
    item_id: str
    kind: str
    title: str
    source: str
    url: str
    published_at: datetime | None = None
    summary: str = ""
    mag: float | None = None
    depth_km: float | None = None
    place: str = ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fold_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def normalize_for_id(value: str) -> str:
    value = fold_accents(clean_text(value)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def make_id(prefix: str, *parts: str) -> str:
    raw = "|".join(normalize_for_id(p) for p in parts if p)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def load_state(path: Path = STATE_FILE) -> dict:
    if not path.exists():
        return {"seen_ids": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"seen_ids": []}
        data.setdefault("seen_ids", [])
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not read state file: {exc}", file=sys.stderr)
        return {"seen_ids": []}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def feed_url_for_query(query: str) -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=es-419&gl=VE&ceid=VE:es-419"


def configured_news_urls() -> list[str]:
    raw = os.getenv("NEWS_RSS_URLS", "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return [feed_url_for_query(q) for q in DEFAULT_NEWS_QUERIES]


def parse_feed_time(entry) -> datetime | None:  # noqa: ANN001
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def extract_source_from_entry(entry) -> str:  # noqa: ANN001
    source = ""
    try:
        source = clean_text(entry.source.title)
    except Exception:
        source = ""
    if source:
        return source
    title = clean_text(getattr(entry, "title", ""))
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return "Google News"


def strip_source_suffix(title: str, source: str) -> str:
    if source and title.endswith(f" - {source}"):
        return title[: -(len(source) + 3)].strip()
    return title


def fetch_news() -> list[Item]:
    cutoff = now_utc() - timedelta(hours=LOOKBACK_HOURS)
    items: list[Item] = []
    seen_titles: set[str] = set()

    for url in configured_news_urls():
        parsed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
        if getattr(parsed, "bozo", False):
            print(f"Warning: feed parser issue for {url}: {getattr(parsed, 'bozo_exception', '')}", file=sys.stderr)

        for entry in parsed.entries:
            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(getattr(entry, "summary", ""))
            link = clean_text(getattr(entry, "link", ""))
            source = extract_source_from_entry(entry)
            title = strip_source_suffix(title, source)
            published_at = parse_feed_time(entry)

            if not title or not link:
                continue
            if published_at and published_at < cutoff:
                continue

            haystack = fold_accents(f"{title} {summary} {source}")
            if not (RELEVANCE_RE.search(haystack) and SEISMIC_RE.search(haystack)):
                continue

            norm_title = normalize_for_id(title)
            if norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

            items.append(
                Item(
                    item_id=make_id("news", title, source),
                    kind="news",
                    title=title,
                    source=source or "Medio",
                    url=link,
                    published_at=published_at,
                    summary=summary,
                )
            )

    items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:MAX_NEWS]


def fetch_usgs_quakes() -> list[Item]:
    start = now_utc() - timedelta(hours=LOOKBACK_HOURS)
    params = {
        "format": "geojson",
        "starttime": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "endtime": now_utc().isoformat(timespec="seconds").replace("+00:00", "Z"),
        "minlatitude": str(MIN_LAT),
        "maxlatitude": str(MAX_LAT),
        "minlongitude": str(MIN_LON),
        "maxlongitude": str(MAX_LON),
        "minmagnitude": str(MIN_MAGNITUDE),
        "orderby": "time",
    }
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    data = response.json()

    items: list[Item] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}
        geometry = feature.get("geometry", {}) or {}
        coordinates = geometry.get("coordinates") or []
        depth = coordinates[2] if len(coordinates) >= 3 else None
        mag = props.get("mag")
        place = clean_text(props.get("place") or "Venezuela / zona cercana")
        event_url = clean_text(props.get("url") or "")
        millis = props.get("time")
        published_at = None
        if isinstance(millis, (int, float)):
            published_at = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)

        title = f"M{mag:.1f} - {place}" if isinstance(mag, (int, float)) else place
        event_id = clean_text(feature.get("id") or make_id("quake", title, event_url))
        items.append(
            Item(
                item_id=f"quake:{event_id}",
                kind="quake",
                title=title,
                source="USGS",
                url=event_url,
                published_at=published_at,
                mag=float(mag) if isinstance(mag, (int, float)) else None,
                depth_km=float(depth) if isinstance(depth, (int, float)) else None,
                place=place,
            )
        )

    items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:MAX_QUAKES]


def local_time(value: datetime | None) -> str:
    if not value:
        return "hora no informada"
    return value.astimezone(DIGEST_TZ).strftime("%d/%m %H:%M")


def detect_priority(items: Iterable[Item]) -> tuple[str, str]:
    max_mag = 0.0
    urgent_news = False
    for item in items:
        if item.mag is not None:
            max_mag = max(max_mag, item.mag)
        if item.kind == "news" and URGENT_RE.search(fold_accents(f"{item.title} {item.summary}")):
            urgent_news = True

    if max_mag >= 5.5 or urgent_news:
        return EMOJI_RED, "Alta"
    if max_mag >= 4.5:
        return EMOJI_YELLOW, "Media"
    return EMOJI_GREEN, "Informativa"


def truncate(value: str, max_len: int = 220) -> str:
    value = clean_text(value)
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "..."


def build_message(new_items: list[Item]) -> str:
    local_now = now_utc().astimezone(DIGEST_TZ).strftime("%d/%m/%Y %H:%M")
    priority_icon, priority_text = detect_priority(new_items)
    quakes = [item for item in new_items if item.kind == "quake"]
    news = [item for item in new_items if item.kind == "news"]

    lines: list[str] = [
        f"{EMOJI_ALERT} Actualizacion sismo Venezuela - {local_now} ART",
        "",
        "Resumen por puntos:",
        f"- Prioridad: {priority_icon} {priority_text}",
        f"- Novedades detectadas: {len(new_items)} ({len(quakes)} sismos / {len(news)} noticias)",
    ]

    if quakes:
        lines.extend(["", f"{EMOJI_GLOBE} Sismos nuevos:"])
        for item in quakes[:MAX_QUAKES]:
            depth = f" - profundidad {item.depth_km:.1f} km" if item.depth_km is not None else ""
            mag = f"M{item.mag:.1f}" if item.mag is not None else "Magnitud no informada"
            lines.append(f"- {mag} - {item.place} - {local_time(item.published_at)} ART{depth}")
            if item.url:
                lines.append(f"  Fuente: {item.url}")

    if news:
        lines.extend(["", f"{EMOJI_NEWS} Noticias nuevas:"])
        for item in news[:MAX_NEWS]:
            source = item.source or "Medio"
            lines.append(f"- [{source}] {truncate(item.title, 180)}")
            if item.url:
                lines.append(f"  {item.url}")

    lines.extend(
        [
            "",
            f"{EMOJI_WARN} Verificar comunicados oficiales antes de reenviar datos sensibles.",
        ]
    )
    return fit_discord_limit("\n".join(lines))


def build_empty_message() -> str:
    local_now = now_utc().astimezone(DIGEST_TZ).strftime("%d/%m/%Y %H:%M")
    return (
        f"{EMOJI_GREEN} Actualizacion sismo Venezuela - {local_now} ART\n\n"
        "- Sin novedades nuevas detectadas en las fuentes monitoreadas.\n"
        "- Se mantiene el seguimiento automatico cada hora."
    )


def fit_discord_limit(message: str, limit: int = 1900) -> str:
    if len(message) <= limit:
        return message
    suffix = "\n\n[Mensaje recortado por limite de Discord]"
    return message[: limit - len(suffix)].rstrip() + suffix


def send_discord(message: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if DRY_RUN:
        print(message)
        return
    if not webhook_url:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL secret/env var")

    payload = {
        "content": message,
        "username": "Sismo Venezuela Monitor",
        "allowed_mentions": {"parse": []},
    }
    response = requests.post(f"{webhook_url}?wait=true", json=payload, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Discord webhook failed: {response.status_code} {response.text[:500]}")


def main() -> int:
    state = load_state()
    seen_ids = list(dict.fromkeys(state.get("seen_ids", [])))
    seen_set = set(seen_ids)

    errors: list[str] = []
    items: list[Item] = []

    try:
        items.extend(fetch_usgs_quakes())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"USGS: {exc}")

    try:
        items.extend(fetch_news())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"News RSS: {exc}")

    # Deduplicate while preserving order.
    unique: list[Item] = []
    local_ids: set[str] = set()
    for item in items:
        if item.item_id not in local_ids:
            unique.append(item)
            local_ids.add(item.item_id)

    new_items = unique if FORCE_SEND else [item for item in unique if item.item_id not in seen_set]

    if errors:
        print("Warnings while fetching sources:", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)

    if not new_items:
        print("No new items detected.")
        if SEND_EMPTY_DIGEST:
            send_discord(build_empty_message())
        return 0

    message = build_message(new_items)
    send_discord(message)

    # Only update state after successful send.
    new_seen = [item.item_id for item in new_items] + seen_ids
    state["seen_ids"] = list(dict.fromkeys(new_seen))[:1000]
    state["last_sent_at"] = now_utc().isoformat(timespec="seconds")
    state["last_sent_count"] = len(new_items)
    save_state(state)

    print(f"Sent digest with {len(new_items)} new items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
