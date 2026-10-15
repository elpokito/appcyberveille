"""
publish.py — Étape 4 du pipeline CyberVeille.

Rôle : mettre l'épisode à disposition de l'app podcast.
  1. envoyer build/episode.mp3 dans une « Release » GitHub (stockage gratuit des fichiers)
  2. tenir à jour la liste des épisodes (data/episodes.json, 30 derniers)
  3. régénérer le flux podcast docs/feed.xml, servi par GitHub Pages
  4. supprimer les Releases trop anciennes
  5. marquer les news de l'épisode comme « vues » (data/seen.json)

Le commit de data/ et docs/ est fait ensuite par le workflow GitHub Actions.

Usage :
  python src/publish.py            → publication réelle (sur GitHub Actions, avec GITHUB_TOKEN)
  py src/publish.py --dry-run      → test local : génère le flux sans rien envoyer ni marquer
"""

import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import mark_seen  # noqa: E402  (même logique de mémoire que la collecte)
from common import gh_warning, job_summary  # noqa: E402

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
EPISODE_FILE = BUILD_DIR / "episode.mp3"
SCRIPT_FILE = BUILD_DIR / "script.json"
NOTES_FILE = BUILD_DIR / "notes.md"
CHAPTERS_FILE = BUILD_DIR / "chapters.json"
EPISODES_FILE = ROOT / "data" / "episodes.json"   # index des épisodes publiés (commité)
SITE_DIR = ROOT / "docs"                          # dossier servi par GitHub Pages

KEEP_EPISODES = 30
# Relance le même jour : on ne remplace PAS un épisode déjà publié par une version bien plus courte
# (ex. une source en panne lors de la relance). FORCE_PUBLISH=1 pour passer outre.
MIN_REPLACE_RATIO = 0.5
OFFLINE_EPISODES = 7      # les 7 plus récents sont aussi copiés sur GitHub Pages (écoute hors ligne dans l'app)
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

# Fournies automatiquement par GitHub Actions (ex. « elpokito/appcyberveille »).
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")

PODCAST = {
    "title": "CyberVeille",
    "description": "Podcast quotidien et automatique de veille en cybersécurité, en français. "
                   "Résumés générés par IA à partir de sources publiques (CERT-FR, CISA, presse spécialisée) : "
                   "vérifiez toujours l'information à la source.",
    "author": "CyberVeille",
    "language": "fr",
}


def site_url() -> str:
    """URL publique GitHub Pages : https://<utilisateur>.github.io/<dépôt>/"""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    owner, _, name = REPO.partition("/")
    return f"https://{owner.lower()}.github.io/{name}/" if owner else "https://example.invalid/"


# --------------------------------------------------------------------------
# GitHub Releases
# --------------------------------------------------------------------------

class GitHub:
    def __init__(self, client: httpx.Client):
        self.c = client
        self.h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.is_error:
            raise RuntimeError(f"GitHub API {r.request.method} {r.request.url.path} → HTTP {r.status_code} : "
                               f"{r.text[:300]}")
        return r

    def delete_release(self, tag: str) -> None:
        """Supprime la Release ET son tag (sinon les tags s'accumulent)."""
        r = self.c.get(f"{API}/repos/{REPO}/releases/tags/{tag}", headers=self.h)
        if r.status_code == 200:
            self._check(self.c.delete(f"{API}/repos/{REPO}/releases/{r.json()['id']}", headers=self.h))
        r = self.c.delete(f"{API}/repos/{REPO}/git/refs/tags/{tag}", headers=self.h)
        if r.status_code not in (204, 404, 422):   # 404/422 : le tag n'existait pas, c'est bon
            self._check(r)

    def publish_mp3(self, tag: str, title: str, mp3: Path, asset_name: str) -> str:
        # Relance le même jour → on remplace proprement l'épisode existant.
        self.delete_release(tag)
        release = self._check(self.c.post(f"{API}/repos/{REPO}/releases", headers=self.h, json={
            "tag_name": tag, "name": title, "body": "Épisode généré automatiquement.",
            "make_latest": "true"})).json()
        self._check(self.c.post(
            f"{UPLOADS}/repos/{REPO}/releases/{release['id']}/assets", params={"name": asset_name},
            headers={**self.h, "Content-Type": "audio/mpeg"}, content=mp3.read_bytes(), timeout=300))
        return f"https://github.com/{REPO}/releases/download/{tag}/{asset_name}"


# --------------------------------------------------------------------------
# Flux podcast (RSS 2.0 + balises Apple Podcasts)
# --------------------------------------------------------------------------

def notes_to_html(md: str) -> str:
    """Convertit nos notes (titres ## et listes de liens) en HTML simple pour l'app podcast."""
    out = []
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# ") or not line:
            continue
        if line.startswith("## "):
            out.append(f"<h3>{html.escape(line[3:])}</h3>")
            continue
        m = re.match(r"- \[(.+)\]\((https?://[^)\s]+)\)(.*)", line)
        if m:
            out.append(f'<p>• <a href="{html.escape(m[2], quote=True)}">{html.escape(m[1])}</a>'
                       f"{html.escape(m[3])}</p>")
        else:
            out.append(f"<p>{html.escape(line.strip('_'))}</p>")
    return "\n".join(out)


def mp3_duration(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return round(float(out))


def hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def build_feed(episodes: list[dict]) -> str:
    base = site_url()
    e = html.escape
    items = []
    for ep in episodes:
        items.append(f"""    <item>
      <title>{e(ep['title'])}</title>
      <guid isPermaLink="false">{e(ep['guid'])}</guid>
      <pubDate>{ep['pub_date']}</pubDate>
      <enclosure url="{e(ep['url'], quote=True)}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{hms(ep['seconds'])}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <podcast:chapters url="{e(base + ep['chapters_path'], quote=True)}" type="application/json+chapters"/>
      <description><![CDATA[{ep['description_html'].replace("]]>", "]]&gt;")}]]></description>
    </item>""")
    now = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{e(PODCAST['title'])}</title>
    <link>{e(base)}</link>
    <atom:link href="{e(base)}feed.xml" rel="self" type="application/rss+xml"/>
    <description>{e(PODCAST['description'])}</description>
    <language>{PODCAST['language']}</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{e(PODCAST['author'])}</itunes:author>
    <itunes:image href="{e(base)}cover.png"/>
    <itunes:category text="Technology"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------

def keeps_existing(existing_seconds: int | None, new_seconds: int, force: bool = False) -> bool:
    """Vrai si l'épisode déjà publié ce jour doit être gardé (le nouveau fait moins de la moitié)."""
    return bool(existing_seconds) and not force and new_seconds < MIN_REPLACE_RATIO * existing_seconds


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    for f in (EPISODE_FILE, SCRIPT_FILE, NOTES_FILE, CHAPTERS_FILE):
        if not f.exists():
            print(f"✗ {f.relative_to(ROOT)} introuvable : lance d'abord les étapes précédentes", file=sys.stderr)
            return 1
    if not dry_run and not (TOKEN and REPO):
        print("✗ GITHUB_TOKEN / GITHUB_REPOSITORY absents : la publication se fait sur GitHub Actions.\n"
              "  En local, utilise --dry-run.", file=sys.stderr)
        return 1

    # En test local, on écrit dans build/ pour ne pas salir docs/ avec de fausses URL.
    site = BUILD_DIR / "site_preview" if dry_run else SITE_DIR

    script = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    date = script["meta"]["date"]
    tag, asset = f"ep-{date}", f"cyberveille-{date}.mp3"
    title = f"CyberVeille du {datetime.fromisoformat(date).strftime('%d/%m/%Y')}"
    episodes = json.loads(EPISODES_FILE.read_text(encoding="utf-8")) if EPISODES_FILE.exists() else []
    seconds = mp3_duration(EPISODE_FILE)

    # 0. Garde-fou : une relance ratée ne doit pas écraser le bon épisode du jour
    existing = next((ep.get("seconds") for ep in episodes if ep["date"] == date), None)
    if keeps_existing(existing, seconds, os.environ.get("FORCE_PUBLISH") == "1"):
        message = (f"Épisode du {date} déjà publié ({hms(existing)}) : la nouvelle version ne dure que "
                   f"{hms(seconds)}, on garde l'ancienne. Pour forcer : variable FORCE_PUBLISH=1.")
        print(f"⚠️  {message}")
        gh_warning(message)
        job_summary(f"### Publication\n⚠️ {message}")
        return 0

    # 1. Envoi du MP3
    if dry_run:
        url = f"https://github.com/{REPO or 'utilisateur/depot'}/releases/download/{tag}/{asset}"
        print(f"[dry-run] MP3 non envoyé (serait publié sur {url})")
    else:
        with httpx.Client(timeout=60) as client:
            url = GitHub(client).publish_mp3(tag, title, EPISODE_FILE, asset)
        print(f"✓ MP3 publié : {url}")

    # 2. Chapitres (format JSON « podcast namespace ») servis par GitHub Pages
    chapters = json.loads(CHAPTERS_FILE.read_text(encoding="utf-8"))
    chapters_path = f"chapters/{date}.json"
    (site / "chapters").mkdir(parents=True, exist_ok=True)
    (site / chapters_path).write_text(json.dumps(
        {"version": "1.2.0", "chapters": [{"startTime": c["start"], "title": c["title"]} for c in chapters]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # 3. Index des épisodes : on remplace celui du jour s'il existe, on garde les 30 plus récents
    episodes = [ep for ep in episodes if ep["date"] != date]
    episodes.insert(0, {
        "date": date, "title": title, "guid": f"cyberveille-{date}", "url": url,
        "bytes": EPISODE_FILE.stat().st_size, "seconds": seconds,
        "pub_date": format_datetime(datetime.now(timezone.utc)),
        "chapters_path": chapters_path, "asset": asset,
        "sources": script["meta"].get("sources", []),
        "description_html": notes_to_html(NOTES_FILE.read_text(encoding="utf-8")),
    })
    episodes.sort(key=lambda ep: ep["date"], reverse=True)
    old, episodes = episodes[KEEP_EPISODES:], episodes[:KEEP_EPISODES]

    # 4. Flux podcast
    site.mkdir(parents=True, exist_ok=True)
    (site / ".nojekyll").touch()   # dit à GitHub Pages de servir les fichiers tels quels
    (site / "feed.xml").write_text(build_feed(episodes), encoding="utf-8")
    # Version publique pour l'app : uniquement des données, aucun HTML (l'app construit l'affichage elle-même).
    public = [{"date": ep["date"], "title": ep["title"], "guid": ep["guid"], "seconds": ep["seconds"],
               "bytes": ep["bytes"], "chapters": ep["chapters_path"], "sources": ep.get("sources", []),
               "audio": f"audio/{ep['asset']}" if i < OFFLINE_EPISODES and "asset" in ep else None,
               "stream": ep["url"]}
              for i, ep in enumerate(episodes)]
    (site / "episodes.json").write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ Flux : {(site / 'feed.xml').relative_to(ROOT)} ({len(episodes)} épisodes) → {site_url()}feed.xml")

    if dry_run:
        print("[dry-run] episodes.json et seen.json non modifiés.")
        return 0

    EPISODES_FILE.write_text(json.dumps(episodes, indent=1, ensure_ascii=False), encoding="utf-8")

    # 5. Ménage des vieux épisodes (fichier MP3 + chapitres)
    if old:
        with httpx.Client(timeout=60) as client:
            gh = GitHub(client)
            for ep in old:
                gh.delete_release(f"ep-{ep['date']}")
                (SITE_DIR / ep["chapters_path"]).unlink(missing_ok=True)
        print(f"✓ {len(old)} ancien(s) épisode(s) supprimé(s)")

    # 6. Seulement maintenant que tout est publié : les news examinées deviennent « vues »
    #    (retenues ou écartées par le rédacteur : elles ne reviendront pas demain comme nouvelles).
    seen = script["meta"].get("seen_ids") or script["meta"]["item_ids"]
    mark_seen([{"id": i} for i in seen])
    print(f"✓ {len(seen)} news marquées comme traitées")
    job_summary(f"### Publication\n- [Écouter l'épisode]({url})\n- App : {site_url()}\n- Flux podcast : {site_url()}feed.xml")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
