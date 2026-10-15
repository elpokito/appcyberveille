"""
collect.py — Étape 1 du pipeline CyberVeille.

Rôle : récupérer les news cyber des dernières 48 h depuis des flux RSS
et le catalogue CISA KEV, les nettoyer, les dédoublonner, puis les écrire
dans data/items.json pour l'étape suivante (generate.py).

Ce script ne parle à aucun LLM et n'utilise aucune clé API.
"""

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from common import gh_warning, job_summary

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
ITEMS_FILE = ROOT / "data" / "items.json"
SEEN_FILE = ROOT / "data" / "seen.json"

# Fenêtre de 48 h et non 24 : certains sites (CERT-FR) datent leurs publications à minuit,
# sans l'heure. Un avis publié à 15 h serait « vieux » de 27 h au passage de 3 h le lendemain,
# et raté avec 24 h. Les doublons d'un jour à l'autre sont bloqués par seen.json.
WINDOW_HOURS = 48
SEEN_RETENTION_DAYS = 14   # au-delà, on oublie qu'on a déjà traité un sujet
MAX_SUMMARY_CHARS = 600    # extrait max envoyé au LLM (économie de tokens + droit d'auteur)
TITLE_SIMILARITY = 0.85    # au-dessus, deux titres sont considérés comme le même sujet
MAX_FAILED_RATIO = 0.50    # plus de la moitié des sources en panne = problème réseau : on arrête
                           # (en dessous : simple avertissement, l'épisode se fait avec les autres)

# Priorité : sert à trier, puis à décider quoi garder si l'épisode est trop long.
SOURCES = [
    {"name": "CERT-FR Alertes",   "url": "https://www.cert.ssi.gouv.fr/alerte/feed/",     "lang": "fr", "priority": 3},
    {"name": "CERT-FR Avis",      "url": "https://www.cert.ssi.gouv.fr/avis/feed/",       "lang": "fr", "priority": 2},
    {"name": "CERT-FR Actualité", "url": "https://www.cert.ssi.gouv.fr/actualite/feed/",  "lang": "fr", "priority": 2},
    {"name": "BleepingComputer",  "url": "https://www.bleepingcomputer.com/feed/",        "lang": "en", "priority": 2},
    {"name": "The Hacker News",   "url": "https://feeds.feedburner.com/TheHackersNews",   "lang": "en", "priority": 2},
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/",             "lang": "en", "priority": 1},
    {"name": "Zataz",             "url": "https://www.zataz.com/feed/",                   "lang": "fr", "priority": 1},
]

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# On s'identifie honnêtement auprès des sites (bonne pratique de scraping poli).
REPO_URL = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'elpokito/appcyberveille')}"
HEADERS = {"User-Agent": f"CyberVeille/1.0 (podcast perso de veille cyber; +{REPO_URL})"}


# --------------------------------------------------------------------------
# Petites fonctions utilitaires
# --------------------------------------------------------------------------

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def normalize_url(url: str) -> str:
    """Retire les paramètres de pistage et le #fragment pour comparer deux liens."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def url_id(url: str) -> str:
    """Empreinte courte et stable d'une URL, utilisée comme identifiant dans seen.json."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


def clean_text(raw: str) -> str:
    """Supprime le HTML, décode les entités (&amp; …) et compacte les espaces."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9à-ÿ ]", "", title.lower())


def entry_date(entry) -> datetime | None:
    """Date de publication d'une entrée RSS, en UTC. None si le flux n'en donne pas."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    return datetime(*struct[:6], tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Mémoire des sujets déjà traités
# --------------------------------------------------------------------------

def load_seen() -> dict:
    if not SEEN_FILE.exists() or SEEN_FILE.stat().st_size == 0:
        return {}
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("⚠️  seen.json illisible, on repart d'une mémoire vide", file=sys.stderr)
        return {}


def mark_seen(items: list[dict]) -> None:
    """
    À appeler APRÈS la publication réussie de l'épisode (dans publish.py).
    Si on marquait les news comme vues ici et que la suite plantait,
    elles seraient perdues pour l'épisode du lendemain.
    """
    seen = load_seen()
    today = datetime.now(timezone.utc).date().isoformat()
    for item in items:
        seen[item["id"]] = today
    limit = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).date().isoformat()
    seen = {k: d for k, d in seen.items() if d >= limit}  # purge des vieilles entrées
    SEEN_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------
# Collecte
# --------------------------------------------------------------------------

def fetch_feed(client: httpx.Client, source: dict, since: datetime) -> list[dict]:
    """Télécharge un flux RSS et renvoie ses news récentes au format commun."""
    response = client.get(source["url"])
    response.raise_for_status()                 # erreur HTTP (404, 500…) → exception
    feed = feedparser.parse(response.content)   # on parse le contenu, pas l'URL : on garde la main sur le réseau

    items = []
    for entry in feed.entries:
        published = entry_date(entry)
        link = entry.get("link")
        if not link or not published or published < since:
            continue
        items.append({
            "id": url_id(link),
            "title": clean_text(entry.get("title", "")),
            "summary": clean_text(entry.get("summary", ""))[:MAX_SUMMARY_CHARS],
            "url": link,
            "source": source["name"],
            "lang": source["lang"],
            "priority": source["priority"],
            "published": published.isoformat(),
        })
    return items


def fetch_kev(client: httpx.Client, since: datetime) -> list[dict]:
    """Failles ajoutées au catalogue CISA KEV (= exploitées activement) dans la fenêtre."""
    data = client.get(KEV_URL).raise_for_status().json()
    items = []
    for vuln in data.get("vulnerabilities", []):
        added = datetime.fromisoformat(vuln["dateAdded"]).replace(tzinfo=timezone.utc)
        # KEV ne donne qu'un jour, pas une heure : on tolère 1 jour de plus pour ne rien rater.
        if added < since - timedelta(days=1):
            continue
        cve = vuln["cveID"]
        items.append({
            "id": url_id(f"kev:{cve}"),
            "title": f"{cve} — {vuln['vendorProject']} {vuln['product']} exploitée activement",
            "summary": clean_text(f"{vuln['vulnerabilityName']}. {vuln['shortDescription']} "
                                  f"Action requise : {vuln['requiredAction']}")[:MAX_SUMMARY_CHARS],
            "url": f"https://nvd.nist.gov/vuln/detail/{cve}",
            "source": "CISA KEV",
            "lang": "en",
            "priority": 3,
            "published": added.isoformat(),
            "cve": cve,
        })
    return items


def deduplicate(items: list[dict], seen: dict) -> list[dict]:
    """Retire les sujets déjà traités les jours précédents et les doublons entre sources."""
    # Les plus prioritaires d'abord : en cas de doublon, c'est la meilleure source qui reste.
    items = sorted(items, key=lambda i: (-i["priority"], i["published"]))
    kept, kept_titles, kept_ids = [], [], set()
    for item in items:
        title = normalize_title(item["title"])
        if item["id"] in seen:
            # Déjà traité un autre jour : on garde son titre en mémoire pour bloquer
            # aussi ses doublons venant d'autres sites (sinon ils reviendraient demain).
            kept_titles.append(title)
            continue
        if item["id"] in kept_ids:
            continue
        if any(SequenceMatcher(None, title, t).ratio() >= TITLE_SIMILARITY for t in kept_titles):
            continue
        kept.append(item)
        kept_ids.add(item["id"])
        kept_titles.append(title)
    return kept


def with_retry(fetch, *args):
    """Un seul nouvel essai après 3 s : beaucoup de pannes réseau ne durent qu'un instant."""
    try:
        return fetch(*args)
    except Exception:
        time.sleep(3)
        return fetch(*args)


def main() -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    collected, failures, counts = [], [], {}

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        jobs = [(s["name"], fetch_feed, (client, s, since)) for s in SOURCES]
        jobs.append(("CISA KEV", fetch_kev, (client, since)))
        for name, fetch, args in jobs:
            try:
                news = with_retry(fetch, *args)
                print(f"✓ {name:<20} {len(news):>3} news")
                counts[name] = len(news)
                collected += news
            except Exception as exc:  # un flux en panne ne doit pas bloquer les autres
                print(f"✗ {name:<20} échec : {exc}", file=sys.stderr)
                counts[name] = "échec"
                failures.append(name)

    items = deduplicate(collected, load_seen())
    ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n→ {len(collected)} news collectées, {len(items)} gardées après dédoublonnage → {ITEMS_FILE.name}")
    job_summary("### Collecte\n| Source | News |\n|---|---|\n"
                + "".join(f"| {n} | {c} |\n" for n, c in counts.items())
                + f"\n**{len(items)}** news nouvelles après dédoublonnage.")

    failed_ratio = len(failures) / len(jobs)
    if failed_ratio > MAX_FAILED_RATIO:
        print(f"✗ {len(failures)} sources en échec ({failed_ratio:.0%}) : {', '.join(failures)}", file=sys.stderr)
        return 1  # code de sortie ≠ 0 → GitHub Actions marque le job en échec et t'envoie un mail
    if failures:
        gh_warning(f"Sources en échec aujourd'hui : {', '.join(failures)} (l'épisode est fait avec les autres)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
