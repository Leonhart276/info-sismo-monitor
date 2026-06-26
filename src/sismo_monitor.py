#!/usr/bin/env python3
"""Hourly earthquake/news monitor for Venezuela -> Discord + email.

Designed for GitHub Actions. It reads public sources, builds alerts when new
items appear, sends a compact version to Discord and the full version by email,
adds a WhatsApp-ready forwarding block, and can send one daily status summary.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import smtplib
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate
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

# Notification controls. "auto" means: enabled only when required secrets exist.
SEND_DISCORD_SETTING = os.getenv("SEND_DISCORD", "auto").lower()
SEND_EMAIL_SETTING = os.getenv("SEND_EMAIL", "auto").lower()
DISCORD_STRICT = os.getenv("DISCORD_STRICT", "false").lower() == "true"
EMAIL_STRICT = os.getenv("EMAIL_STRICT", "false").lower() == "true"
DISCORD_MESSAGE_MODE = os.getenv("DISCORD_MESSAGE_MODE", "compact").lower()
DISCORD_MAX_CHARS = int(os.getenv("DISCORD_MAX_CHARS", "1800"))

# Optional URL shortening for short Discord/WhatsApp-friendly messages.
SHORTEN_URLS_FOR_DISCORD = os.getenv("SHORTEN_URLS_FOR_DISCORD", "true").lower() == "true"
SHORTEN_URLS_FOR_FORWARD = os.getenv("SHORTEN_URLS_FOR_FORWARD", "true").lower() == "true"
URL_SHORTENER = os.getenv("URL_SHORTENER", "isgd").strip().lower()
URL_SHORTENER_TIMEOUT = int(os.getenv("URL_SHORTENER_TIMEOUT", "8"))
URL_CACHE_MAX_ENTRIES = int(os.getenv("URL_CACHE_MAX_ENTRIES", "500"))

# Daily status summary controls.
DAILY_SUMMARY_ENABLED = os.getenv("DAILY_SUMMARY_ENABLED", "true").lower() == "true"
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "9"))
DAILY_SUMMARY_TO_DISCORD = os.getenv("DAILY_SUMMARY_TO_DISCORD", "true").lower() == "true"
DAILY_SUMMARY_TO_EMAIL = os.getenv("DAILY_SUMMARY_TO_EMAIL", "true").lower() == "true"
FORCE_DAILY_SUMMARY = os.getenv("FORCE_DAILY_SUMMARY", "false").lower() == "true"
FORWARD_BLOCK_ENABLED = os.getenv("FORWARD_BLOCK_ENABLED", "true").lower() == "true"

# Broad bounding box around Venezuela and nearby offshore areas.
MIN_LAT = float(os.getenv("MIN_LAT", "0.0"))
MAX_LAT = float(os.getenv("MAX_LAT", "13.8"))
MIN_LON = float(os.getenv("MIN_LON", "-74.8"))
MAX_LON = float(os.getenv("MAX_LON", "-58.8"))

USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "sismo-venezuela-discord-email-monitor/1.2 (+https://github.com/)",
)

EMOJI_ALERT = "\U0001F6A8"
EMOJI_RED = "\U0001F534"
EMOJI_YELLOW = "\U0001F7E1"
EMOJI_GREEN = "\U0001F7E2"
EMOJI_GLOBE = "\U0001F30E"
EMOJI_NEWS = "\U0001F4F0"
EMOJI_MAIL = "\U0001F4E7"
EMOJI_WARN = "\u26A0\uFE0F"
EMOJI_PIN = "\U0001F4CC"
EMOJI_PHONE = "\U0001F4F2"
EMOJI_LINK = "\U0001F517"
EMOJI_CHECK = "\u2705"

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


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def load_state(path: Path = STATE_FILE) -> dict:
    if not path.exists():
        return {"seen_ids": [], "url_cache": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"seen_ids": [], "url_cache": {}}
        data.setdefault("seen_ids", [])
        data.setdefault("url_cache", {})
        if not isinstance(data.get("url_cache"), dict):
            data["url_cache"] = {}
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not read state file: {exc}", file=sys.stderr)
        return {"seen_ids": [], "url_cache": {}}


def trim_url_cache(cache: dict[str, str]) -> dict[str, str]:
    if len(cache) <= URL_CACHE_MAX_ENTRIES:
        return cache
    items = list(cache.items())[-URL_CACHE_MAX_ENTRIES:]
    return dict(items)


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["url_cache"] = trim_url_cache(dict(state.get("url_cache", {})))
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
            print(
                f"Warning: feed parser issue for {url}: {getattr(parsed, 'bozo_exception', '')}",
                file=sys.stderr,
            )

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


def local_now_dt() -> datetime:
    return now_utc().astimezone(DIGEST_TZ)


def local_now_label(include_year: bool = True) -> str:
    fmt = "%d/%m/%Y %H:%M" if include_year else "%d/%m %H:%M"
    return local_now_dt().strftime(fmt)


def local_today_key() -> str:
    return local_now_dt().strftime("%Y-%m-%d")


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


def get_item_url(item: Item, link_map: dict[str, str] | None = None) -> str:
    if link_map and item.item_id in link_map:
        return link_map[item.item_id]
    return item.url


def shorten_url(url: str, cache: dict[str, str] | None = None) -> str:
    """Return a shortened URL when possible; never fail the monitor because of this."""
    url = clean_text(url)
    if not url or not is_http_url(url):
        return url
    if URL_SHORTENER in {"", "none", "off", "false"}:
        return url
    if cache is not None and url in cache:
        return cache[url]

    short_url = url
    try:
        service = URL_SHORTENER
        endpoint = "https://is.gd/create.php"
        if service in {"vgd", "v.gd"}:
            endpoint = "https://v.gd/create.php"
        elif service not in {"isgd", "is.gd"}:
            print(f"Warning: unsupported URL_SHORTENER={service}; using original URL.", file=sys.stderr)
            return url

        response = requests.get(
            endpoint,
            params={"format": "simple", "url": url},
            headers={"User-Agent": USER_AGENT},
            timeout=URL_SHORTENER_TIMEOUT,
        )
        if response.status_code < 400:
            candidate = clean_text(response.text)
            if is_http_url(candidate) and len(candidate) < len(url):
                short_url = candidate
        else:
            print(
                f"Warning: URL shortener returned HTTP {response.status_code}; using original URL.",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: URL shortener failed: {exc}; using original URL.", file=sys.stderr)

    if cache is not None:
        cache[url] = short_url
    return short_url


def build_link_map(
    items: Iterable[Item],
    cache: dict[str, str] | None = None,
    *,
    shorten: bool = False,
) -> dict[str, str]:
    links: dict[str, str] = {}
    for item in items:
        if not item.url:
            continue
        links[item.item_id] = shorten_url(item.url, cache) if shorten else item.url
    return links


def quake_line(
    item: Item,
    *,
    with_url: bool = False,
    link_map: dict[str, str] | None = None,
) -> list[str]:
    depth = f" - profundidad {item.depth_km:.1f} km" if item.depth_km is not None else ""
    mag = f"M{item.mag:.1f}" if item.mag is not None else "Magnitud no informada"
    lines = [f"- {mag} - {item.place} - {local_time(item.published_at)} ART{depth}"]
    link = get_item_url(item, link_map)
    if with_url and link:
        lines.append(f"  Fuente USGS: <{link}>")
    return lines


def build_forward_block(
    items: list[Item],
    *,
    link_map: dict[str, str] | None = None,
    title: str = "Texto corto para reenviar",
    max_items: int = 4,
) -> list[str]:
    priority_icon, priority_text = detect_priority(items)
    quakes = [item for item in items if item.kind == "quake"]
    news = [item for item in items if item.kind == "news"]

    lines: list[str] = [
        f"{EMOJI_PHONE} {title}:",
        "",
        f"{EMOJI_ALERT} Actualizacion sismo Venezuela - {local_now_label(include_year=False)} ART",
        f"- Prioridad: {priority_icon} {priority_text}",
    ]

    selected: list[Item] = []
    selected.extend(quakes[:2])
    selected.extend(news[: max(0, max_items - len(selected))])

    if selected:
        for item in selected[:max_items]:
            link = get_item_url(item, link_map)
            if item.kind == "quake":
                mag = f"M{item.mag:.1f}" if item.mag is not None else "sismo"
                line = f"- {mag} - {truncate(item.place, 85)} - {local_time(item.published_at)} ART"
            else:
                line = f"- {truncate(item.title, 110)} ({item.source or 'Medio'})"
            lines.append(line)
            if link:
                lines.append(f"  Fuente: {link}")
    else:
        lines.append("- Sin novedades relevantes detectadas en las ultimas 24 h.")

    if len(items) > len(selected):
        lines.append(f"- +{len(items) - len(selected)} novedades adicionales en el resumen completo.")

    lines.append("- Verificar fuentes oficiales antes de compartir.")
    return lines


def build_full_message(
    new_items: list[Item],
    *,
    link_map: dict[str, str] | None = None,
    forward_link_map: dict[str, str] | None = None,
) -> str:
    priority_icon, priority_text = detect_priority(new_items)
    quakes = [item for item in new_items if item.kind == "quake"]
    news = [item for item in new_items if item.kind == "news"]

    lines: list[str] = [
        f"{EMOJI_ALERT} Actualizacion sismo Venezuela - {local_now_label()} ART",
        "",
        "Resumen por puntos:",
        f"- Prioridad: {priority_icon} {priority_text}",
        f"- Novedades detectadas: {len(new_items)} ({len(quakes)} sismos / {len(news)} noticias)",
    ]

    if quakes:
        lines.extend(["", f"{EMOJI_GLOBE} Sismos nuevos:"])
        for item in quakes[:MAX_QUAKES]:
            lines.extend(quake_line(item, with_url=True, link_map=link_map))

    if news:
        lines.extend(["", f"{EMOJI_NEWS} Noticias nuevas:"])
        for item in news[:MAX_NEWS]:
            source = item.source or "Medio"
            lines.append(f"- [{source}] {truncate(item.title, 220)}")
            link = get_item_url(item, link_map)
            if link:
                lines.append(f"  Fuente: {link}")

    if FORWARD_BLOCK_ENABLED:
        lines.extend(["", *build_forward_block(new_items, link_map=forward_link_map)])

    lines.extend(
        [
            "",
            f"{EMOJI_WARN} Verificar comunicados oficiales antes de reenviar datos sensibles.",
        ]
    )
    return "\n".join(lines)


def build_compact_discord_message(
    new_items: list[Item],
    *,
    email_enabled: bool,
    link_map: dict[str, str] | None = None,
) -> str:
    priority_icon, priority_text = detect_priority(new_items)
    quakes = [item for item in new_items if item.kind == "quake"]
    news = [item for item in new_items if item.kind == "news"]

    lines: list[str] = [
        f"{EMOJI_ALERT} Actualizacion sismo Venezuela - {local_now_label()} ART",
        f"- Prioridad: {priority_icon} {priority_text}",
        f"- Novedades: {len(new_items)} ({len(quakes)} sismos / {len(news)} noticias)",
    ]

    if quakes:
        lines.append("")
        lines.append(f"{EMOJI_GLOBE} Sismos destacados:")
        for item in quakes[:3]:
            lines.extend(quake_line(item, with_url=True, link_map=link_map))
        if len(quakes) > 3:
            lines.append(f"- +{len(quakes) - 3} sismos adicionales en el resumen completo.")

    if news:
        lines.append("")
        lines.append(f"{EMOJI_NEWS} Noticias destacadas:")
        for item in news[:4]:
            source = item.source or "Medio"
            lines.append(f"- [{source}] {truncate(item.title, 120)}")
            link = get_item_url(item, link_map)
            if link:
                lines.append(f"  {EMOJI_LINK} <{link}>")
        if len(news) > 4:
            lines.append(f"- +{len(news) - 4} noticias adicionales en el resumen completo.")

    lines.append("")
    if email_enabled:
        lines.append(f"{EMOJI_MAIL} Resumen completo enviado por correo.")
    elif DISCORD_MESSAGE_MODE == "compact":
        lines.append(f"{EMOJI_MAIL} Correo no configurado; Discord muestra resumen compacto.")
    lines.append(f"{EMOJI_WARN} Verificar fuentes oficiales antes de reenviar.")
    return "\n".join(lines)


def build_empty_message() -> str:
    return (
        f"{EMOJI_GREEN} Actualizacion sismo Venezuela - {local_now_label()} ART\n\n"
        "- Sin novedades nuevas detectadas en las fuentes monitoreadas.\n"
        "- Se mantiene el seguimiento automatico cada hora."
    )


def newest_item(items: list[Item]) -> Item | None:
    dated = [item for item in items if item.published_at]
    if not dated:
        return items[0] if items else None
    return max(dated, key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc))


def max_magnitude_label(quakes: list[Item]) -> str:
    mags = [item.mag for item in quakes if item.mag is not None]
    if not mags:
        return "sin datos"
    return f"M{max(mags):.1f}"


def build_daily_summary_message(
    items_24h: list[Item],
    *,
    errors: list[str] | None = None,
    link_map: dict[str, str] | None = None,
    forward_link_map: dict[str, str] | None = None,
) -> str:
    errors = errors or []
    quakes = [item for item in items_24h if item.kind == "quake"]
    news = [item for item in items_24h if item.kind == "news"]
    priority_icon, priority_text = detect_priority(items_24h)
    latest = newest_item(items_24h)

    lines: list[str] = [
        f"{EMOJI_PIN} Resumen diario - Sismo Venezuela - {local_now_label()} ART",
        "",
        "Ultimas 24 h:",
        f"- Estado: {priority_icon} {priority_text}",
        f"- Eventos sismicos detectados: {len(quakes)}",
        f"- Mayor magnitud registrada: {max_magnitude_label(quakes)}",
        f"- Noticias relevantes detectadas: {len(news)}",
        "- Fuentes revisadas: USGS + RSS de noticias configurados",
    ]

    if latest:
        lines.append(f"- Ultima novedad detectada: {truncate(latest.title, 140)} - {local_time(latest.published_at)} ART")
    else:
        lines.append("- Ultima novedad detectada: sin novedades relevantes en las fuentes monitoreadas.")

    if errors:
        lines.append(f"- Avisos tecnicos: {len(errors)} fuente(s) respondieron con advertencias. Revisar logs de GitHub Actions.")

    if quakes:
        lines.extend(["", f"{EMOJI_GLOBE} Sismos relevantes:"])
        for item in quakes[:MAX_QUAKES]:
            lines.extend(quake_line(item, with_url=True, link_map=link_map))

    if news:
        lines.extend(["", f"{EMOJI_NEWS} Noticias relevantes:"])
        for item in news[:MAX_NEWS]:
            source = item.source or "Medio"
            lines.append(f"- [{source}] {truncate(item.title, 220)}")
            link = get_item_url(item, link_map)
            if link:
                lines.append(f"  Fuente: {link}")

    if FORWARD_BLOCK_ENABLED:
        lines.extend(
            [
                "",
                *build_forward_block(
                    items_24h,
                    link_map=forward_link_map,
                    title="Texto corto para reenviar",
                ),
            ]
        )

    lines.extend(
        [
            "",
            f"{EMOJI_CHECK} Monitoreo automatico activo.",
            f"{EMOJI_WARN} Verificar comunicados oficiales antes de reenviar datos sensibles.",
        ]
    )
    return "\n".join(lines)


def build_daily_summary_discord(
    items_24h: list[Item],
    *,
    errors: list[str] | None = None,
    link_map: dict[str, str] | None = None,
    email_enabled: bool = False,
) -> str:
    errors = errors or []
    quakes = [item for item in items_24h if item.kind == "quake"]
    news = [item for item in items_24h if item.kind == "news"]
    priority_icon, priority_text = detect_priority(items_24h)
    latest = newest_item(items_24h)

    lines: list[str] = [
        f"{EMOJI_PIN} Resumen diario sismo Venezuela - {local_now_label()} ART",
        f"- Estado: {priority_icon} {priority_text}",
        f"- Sismos 24 h: {len(quakes)} | Mayor magnitud: {max_magnitude_label(quakes)}",
        f"- Noticias 24 h: {len(news)}",
    ]

    if latest:
        lines.append(f"- Ultima novedad: {truncate(latest.title, 100)}")
    else:
        lines.append("- Sin novedades relevantes en las ultimas 24 h.")

    if quakes:
        lines.append("")
        lines.append(f"{EMOJI_GLOBE} Sismos principales:")
        for item in quakes[:2]:
            lines.extend(quake_line(item, with_url=True, link_map=link_map))

    if news:
        lines.append("")
        lines.append(f"{EMOJI_NEWS} Noticias principales:")
        for item in news[:3]:
            lines.append(f"- [{item.source or 'Medio'}] {truncate(item.title, 105)}")
            link = get_item_url(item, link_map)
            if link:
                lines.append(f"  {EMOJI_LINK} <{link}>")

    if errors:
        lines.append("")
        lines.append(f"{EMOJI_WARN} Hubo {len(errors)} aviso(s) tecnico(s). Revisar logs.")

    lines.append("")
    if email_enabled:
        lines.append(f"{EMOJI_MAIL} Detalle completo enviado por correo.")
    else:
        lines.append(f"{EMOJI_MAIL} Correo no configurado; Discord muestra resumen diario compacto.")
    lines.append(f"{EMOJI_WARN} Verificar fuentes oficiales antes de reenviar.")
    return "\n".join(lines)


def build_subject(new_items: list[Item]) -> str:
    if not new_items:
        return f"Sismo Venezuela - sin novedades - {local_now_label(include_year=False)} ART"
    _, priority_text = detect_priority(new_items)
    quakes = [item for item in new_items if item.kind == "quake"]
    news = [item for item in new_items if item.kind == "news"]
    return (
        f"Sismo Venezuela - {priority_text} - {len(new_items)} novedades "
        f"({len(quakes)} sismos / {len(news)} noticias) - {local_now_label(include_year=False)} ART"
    )


def build_daily_subject(items_24h: list[Item]) -> str:
    quakes = [item for item in items_24h if item.kind == "quake"]
    news = [item for item in items_24h if item.kind == "news"]
    _, priority_text = detect_priority(items_24h)
    if not items_24h:
        return f"Resumen diario - Sismo Venezuela - sin novedades - {local_now_label(include_year=False)} ART"
    return (
        f"Resumen diario - Sismo Venezuela - {priority_text} - "
        f"{len(quakes)} sismos / {len(news)} noticias - {local_now_label(include_year=False)} ART"
    )


def setting_enabled(setting: str, required_values: Iterable[str]) -> bool:
    value = (setting or "auto").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return all((v or "").strip() for v in required_values)


def email_required_values() -> list[str]:
    return [
        os.getenv("SMTP_HOST", ""),
        os.getenv("SMTP_USERNAME", ""),
        os.getenv("SMTP_PASSWORD", ""),
        os.getenv("EMAIL_TO", ""),
    ]


def is_email_enabled() -> bool:
    return setting_enabled(SEND_EMAIL_SETTING, email_required_values())


def is_discord_enabled() -> bool:
    return setting_enabled(SEND_DISCORD_SETTING, [os.getenv("DISCORD_WEBHOOK_URL", "")])


def split_recipients(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def split_text_for_discord(message: str, limit: int = DISCORD_MAX_CHARS) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in message.splitlines():
        # Split extremely long individual lines, usually URLs.
        parts = [line]
        if len(line) > limit:
            parts = [line[i : i + limit - 20] for i in range(0, len(line), limit - 20)]

        for part in parts:
            addition = len(part) + (1 if current else 0)
            if current and current_len + addition > limit:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(part)
            current_len += len(part) + (1 if current_len else 0)

    if current:
        chunks.append("\n".join(current))

    return chunks


def send_discord(message: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL secret/env var")

    chunks = split_text_for_discord(message, DISCORD_MAX_CHARS)

    if DRY_RUN:
        print("\n--- Discord preview ---")
        for index, chunk in enumerate(chunks, start=1):
            label = f" part {index}/{len(chunks)}" if len(chunks) > 1 else ""
            print(f"[Discord{label}]\n{chunk}\n")
        return

    for index, chunk in enumerate(chunks, start=1):
        content = chunk
        if len(chunks) > 1:
            suffix = f"\n\n(Parte {index}/{len(chunks)})"
            if len(content) + len(suffix) <= DISCORD_MAX_CHARS:
                content += suffix
        payload = {
            "content": content,
            "username": "Sismo Venezuela Monitor",
            "allowed_mentions": {"parse": []},
            "flags":4,
        }
        response = requests.post(f"{webhook_url}?wait=true", json=payload, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Discord webhook failed: {response.status_code} {response.text[:500]}")


def send_email(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_addr = os.getenv("EMAIL_FROM", "").strip() or username
    to_addrs = split_recipients(os.getenv("EMAIL_TO", ""))
    reply_to = os.getenv("EMAIL_REPLY_TO", "").strip()
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    missing = []
    if not host:
        missing.append("SMTP_HOST")
    if not username:
        missing.append("SMTP_USERNAME")
    if not password:
        missing.append("SMTP_PASSWORD")
    if not from_addr:
        missing.append("EMAIL_FROM")
    if not to_addrs:
        missing.append("EMAIL_TO")
    if missing:
        raise RuntimeError(f"Missing email config: {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=False)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    if DRY_RUN:
        print("\n--- Email preview ---")
        print(f"To: {', '.join(to_addrs)}")
        print(f"Subject: {subject}")
        print(body)
        return

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            server.login(username, password)
            server.send_message(msg)


def send_notifications(
    full_message: str,
    discord_message: str,
    subject: str,
    *,
    use_discord: bool = True,
    use_email: bool = True,
) -> list[str]:
    """Send notifications. Caller updates state after at least one channel succeeds."""
    successes: list[str] = []
    failures: list[tuple[str, Exception]] = []

    if use_discord and is_discord_enabled():
        try:
            message = full_message if DISCORD_MESSAGE_MODE == "full" else discord_message
            send_discord(message)
            successes.append("Discord")
        except Exception as exc:  # noqa: BLE001
            failures.append(("Discord", exc))
    elif use_discord:
        print("Discord not configured/enabled; skipping Discord send.")

    if use_email and is_email_enabled():
        try:
            send_email(subject, full_message)
            successes.append("Email")
        except Exception as exc:  # noqa: BLE001
            failures.append(("Email", exc))
    elif use_email:
        print("Email not configured/enabled; skipping email send.")

    for channel, exc in failures:
        print(f"Warning: {channel} send failed: {exc}", file=sys.stderr)

    if any(channel == "Discord" for channel, _ in failures) and DISCORD_STRICT:
        raise RuntimeError("Discord send failed and DISCORD_STRICT=true")
    if any(channel == "Email" for channel, _ in failures) and EMAIL_STRICT:
        raise RuntimeError("Email send failed and EMAIL_STRICT=true")

    if not successes:
        if failures:
            details = "; ".join(f"{channel}: {exc}" for channel, exc in failures)
            raise RuntimeError(f"All notification channels failed: {details}")
        raise RuntimeError("No notification channel configured/enabled for this message.")

    print(f"Notification sent through: {', '.join(successes)}")
    return successes


def should_send_daily_summary(state: dict) -> bool:
    if FORCE_DAILY_SUMMARY:
        return True
    if not DAILY_SUMMARY_ENABLED:
        return False
    today = local_today_key()
    if state.get("last_daily_summary_date") == today:
        return False
    return local_now_dt().hour >= DAILY_SUMMARY_HOUR


def deduplicate_items(items: list[Item]) -> list[Item]:
    unique: list[Item] = []
    local_ids: set[str] = set()
    for item in items:
        if item.item_id not in local_ids:
            unique.append(item)
            local_ids.add(item.item_id)
    return unique


def make_link_maps(items: list[Item], url_cache: dict[str, str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    full_links = build_link_map(items, shorten=False)
    discord_links = build_link_map(items, url_cache, shorten=SHORTEN_URLS_FOR_DISCORD)
    forward_links = build_link_map(items, url_cache, shorten=SHORTEN_URLS_FOR_FORWARD)
    return full_links, discord_links, forward_links


def main() -> int:
    state = load_state()
    seen_ids = list(dict.fromkeys(state.get("seen_ids", [])))
    seen_set = set(seen_ids)
    url_cache: dict[str, str] = dict(state.get("url_cache", {}))

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

    unique = deduplicate_items(items)
    new_items = unique if FORCE_SEND else [item for item in unique if item.item_id not in seen_set]

    if errors:
        print("Warnings while fetching sources:", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)

    state_changed = False

    if new_items:
        full_links, discord_links, forward_links = make_link_maps(new_items, url_cache)
        full_message = build_full_message(
            new_items,
            link_map=full_links,
            forward_link_map=forward_links,
        )
        discord_message = build_compact_discord_message(
            new_items,
            email_enabled=is_email_enabled(),
            link_map=discord_links,
        )
        send_notifications(full_message, discord_message, build_subject(new_items))

        # Only update seen state after at least one successful notification channel.
        new_seen = [item.item_id for item in new_items] + seen_ids
        state["seen_ids"] = list(dict.fromkeys(new_seen))[:1000]
        state["last_sent_at"] = now_utc().isoformat(timespec="seconds")
        state["last_sent_count"] = len(new_items)
        state_changed = True
        print(f"Sent digest with {len(new_items)} new items.")
    else:
        print("No new items detected.")
        if SEND_EMPTY_DIGEST:
            full_message = build_empty_message()
            send_notifications(full_message, full_message, build_subject([]))

    if should_send_daily_summary(state):
        full_links, discord_links, forward_links = make_link_maps(unique, url_cache)
        daily_full = build_daily_summary_message(
            unique,
            errors=errors,
            link_map=full_links,
            forward_link_map=forward_links,
        )
        daily_discord = build_daily_summary_discord(
            unique,
            errors=errors,
            link_map=discord_links,
            email_enabled=DAILY_SUMMARY_TO_EMAIL and is_email_enabled(),
        )
        try:
            send_notifications(
                daily_full,
                daily_discord,
                build_daily_subject(unique),
                use_discord=DAILY_SUMMARY_TO_DISCORD,
                use_email=DAILY_SUMMARY_TO_EMAIL,
            )
            state["last_daily_summary_date"] = local_today_key()
            state["last_daily_summary_at"] = now_utc().isoformat(timespec="seconds")
            state["last_daily_summary_count"] = len(unique)
            state_changed = True
            print(f"Sent daily summary with {len(unique)} items from the last {LOOKBACK_HOURS} h.")
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: daily summary failed: {exc}", file=sys.stderr)
            if not state_changed:
                raise

    state["url_cache"] = trim_url_cache(url_cache)
    if state_changed:
        save_state(state)
    else:
        print("No state changes to save.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
