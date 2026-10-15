"""
tts.py — Étape 3 du pipeline CyberVeille.

Rôle : transformer build/script.json (produit par generate.py) en un MP3 prêt à écouter :
  1. nettoyer le texte pour l'oral (CVE, sigles, scores CVSS…)
  2. faire lire chaque passage par une voix française (edge-tts, gratuit)
  3. assembler les morceaux avec de courts silences, normaliser le volume,
     et ajouter des CHAPITRES (sauter une rubrique dans l'app podcast)

Sorties :
  - build/episode.mp3    : l'épisode
  - build/chapters.json  : début de chaque rubrique (servira au flux RSS)

Usage :
  py src/tts.py            → génère l'épisode
  py src/tts.py --samples  → génère un court extrait avec chaque voix, pour choisir
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import edge_tts

from common import job_summary

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
SCRIPT_FILE = BUILD_DIR / "script.json"
PARTS_DIR = BUILD_DIR / "parts"            # un MP3 par passage, avant assemblage
EPISODE_FILE = BUILD_DIR / "episode.mp3"
CHAPTERS_FILE = BUILD_DIR / "chapters.json"

VOICE = os.environ.get("TTS_VOICE", "fr-FR-VivienneMultilingualNeural")   # choisie le 15/10 ; réglable via TTS_VOICE
RATE = os.environ.get("TTS_RATE", "+5%")                    # un poil plus rapide que la normale
SAMPLE_VOICES = ["fr-FR-DeniseNeural", "fr-FR-HenriNeural",
                 "fr-FR-VivienneMultilingualNeural", "fr-FR-RemyMultilingualNeural"]

SAMPLE_RATE = 24000       # fréquence de sortie d'edge-tts : inutile de monter plus haut
BITRATE = "64k"           # mono 64 kbit/s ≈ 14 Mo pour 30 min : léger pour le téléphone, clair pour la voix
SHORT_PAUSE = 0.6         # secondes entre deux passages (si pas de jingle)
LONG_PAUSE = 1.3          # secondes entre deux rubriques (si pas de jingle)
JINGLE_GAP = 0.35         # petit silence de part et d'autre d'un jingle, pour qu'il « respire »

# Jingles au violon, générés par ffmpeg (aucun fichier externe, aucun droit d'auteur).
# Pour utiliser ton propre son, dépose assets/jingle_news.mp3
# (libre de droits, ex. licence CC0) : il remplace automatiquement celui généré.
# Mettre JINGLES=0 dans l'environnement pour revenir aux simples silences.
JINGLES = os.environ.get("JINGLES", "1") != "0"
ASSETS_DIR = ROOT / "assets"


# Violon synthétisé : une corde riche en harmoniques, jouée à l'archet (attaque progressive,
# vibrato qui arrive après l'attaque, relâchement doux), avec une légère réverbération.
VIOLIN_HARMONICS = [1, 0.55, 0.4, 0.28, 0.22, 0.15, 0.12, 0.08, 0.06, 0.04]
G4, C5, E5 = 392.0, 523.25, 659.25


def _bowed(freq: float, start: float, length: float, gain: float) -> str:
    """Une note de violon à l'archet, sous forme d'expression ffmpeg (aevalsrc)."""
    t = f"(t-{start})"
    vibrato = f"(min({t},0.25)/0.25)*{0.006 * freq / 5.5:.4f}*sin(2*PI*5.5*{t})"   # 5,5 Hz, ±0,6 %
    tone = "+".join(f"{a}*sin(2*PI*{freq * n}*{t}+{n}*{vibrato})" for n, a in enumerate(VIOLIN_HARMONICS, 1))
    envelope = f"(1-exp(-14*{t}))*if(lt({t},{length}),1,exp(-9*({t}-{length})))"
    return f"{gain}*gte(t,{start})*{envelope}*({tone})"


# Entre deux news : sol - do - mi, liés (1,8 s).
NEWS_JINGLE = (" + ".join([_bowed(G4, 0, 0.22, 0.12), _bowed(C5, 0.24, 0.22, 0.12),
                           _bowed(E5, 0.48, 0.5, 0.12)]), 1.8)
VIOLIN_FX = ("highpass=f=180,lowpass=f=4500,"                                   # couleur « bois »
             "aecho=0.8:0.8:60|120|190|260:0.4|0.3|0.2|0.12,"                    # réverbération légère
             "loudnorm=I=-21:TP=-3")                                             # un peu sous la voix
TTS_RETRIES = 3           # le service est en ligne : une coupure réseau ne doit pas tout faire échouer
PARALLEL = 4              # passages synthétisés en même temps (plus rapide, sans abuser du service)

# --------------------------------------------------------------------------
# 1. Nettoyage du texte pour l'oral
# --------------------------------------------------------------------------

# Sigles à épeler ou à prononcer « à la française ». Complète cette liste au fil des écoutes.
PRONUNCIATION = {
    r"\bCERT-FR\b": "CERT France",
    r"\bANSSI\b": "Anssi",
    r"\bCISA\b": "Cisa",
    r"\bNIST\b": "Nist",
    r"\bKEV\b": "K.E.V.",
    r"\bRCE\b": "R.C.E.",
    r"\bXSS\b": "X.S.S.",
    r"\bSQLi\b": "injection SQL",
    r"\bSQL\b": "S.Q.L.",
    r"\bMFA\b": "M.F.A.",
    r"\b2FA\b": "double authentification",
    r"\bIoT\b": "I.O.T.",
    r"\bAPT(\d+)\b": r"A.P.T. \1",
    r"\bAPT\b": "A.P.T.",
    r"\bC2\b": "C. 2",
    r"\bzero[- ]day\b": "zéro day",
    r"\b0[- ]day\b": "zéro day",
    r"\bWi-?Fi\b": "wifi",
    r"\bSSH\b": "S.S.H.",
    r"\bSSL\b": "S.S.L.",
    r"\bTLS\b": "T.L.S.",
    r"\bAI\b": "I.A.",
}


# Caractères typographiques que le LLM utilise et que la synthèse vocale lit mal (pause, « tiret »…).
TYPOGRAPHY = {"\u2011": "-", "\u2010": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u00ad": ""}


def clean_for_speech(text: str) -> str:
    for char, plain in TYPOGRAPHY.items():
        text = text.replace(char, plain)
    # CVE-2026-12345 → « CVE 2026, 12345 » : sinon les tirets sont lus « moins ».
    text = re.sub(r"\bCVE-(\d{4})-(\d+)\b", r"C.V.E. \1, \2", text, flags=re.IGNORECASE)
    # Score CVSS 9.8 → « 9 virgule 8 » (en français, le point décimal se lit mal).
    text = re.sub(r"(CVSS[^0-9]{0,15})(\d+)\.(\d)", r"\1\2 virgule \3", text)
    # Versions : v2.4.1 → « version 2.4.1 »
    text = re.sub(r"\bv(\d+(?:\.\d+)+)\b", r"version \1", text)
    for pattern, spoken in PRONUNCIATION.items():
        text = re.sub(pattern, spoken, text)
    # Restes de markdown ou caractères qui s'entendraient bizarrement
    text = re.sub(r"[*_#`>|]", " ", text)
    text = re.sub(r"\.(\s*\.)+", ".", text)   # « R.C.E.. » → « R.C.E. »
    return " ".join(text.split())


# --------------------------------------------------------------------------
# 2. Synthèse vocale
# --------------------------------------------------------------------------

async def synthesize(text: str, path: Path, voice: str = VOICE) -> None:
    """Fait lire un passage ; réessaie en cas de coupure ; refuse un fichier vide."""
    for attempt in range(1, TTS_RETRIES + 1):
        try:
            await edge_tts.Communicate(text, voice, rate=RATE).save(str(path))
            if path.exists() and path.stat().st_size > 1000:   # un échec peut laisser un fichier vide
                return
            raise RuntimeError("fichier audio vide")
        except Exception as exc:
            path.unlink(missing_ok=True)
            if attempt == TTS_RETRIES:
                raise RuntimeError(f"synthèse impossible après {TTS_RETRIES} essais : {exc!r}") from exc
            await asyncio.sleep(2 * attempt)   # on laisse respirer le service avant de réessayer


async def synthesize_all(jobs: list[tuple[str, Path]]) -> None:
    limit = asyncio.Semaphore(PARALLEL)
    done = 0

    async def one(text: str, path: Path) -> None:
        nonlocal done
        async with limit:
            await synthesize(text, path)
        done += 1
        print(f"\r   voix : {done}/{len(jobs)} passages", end="", flush=True)

    await asyncio.gather(*(one(t, p) for t, p in jobs))
    print()


# --------------------------------------------------------------------------
# 3. Assemblage avec ffmpeg
# --------------------------------------------------------------------------

def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} a échoué :\n{result.stderr[-800:]}")
    return result.stdout


def duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())


# On assemble en WAV (son brut, non compressé) puis on encode UNE seule fois en MP3 :
# les MP3 mis bout à bout ajoutent chacun quelques millisecondes de « bourrage »,
# ce qui décalerait les chapitres de plusieurs secondes en fin d'épisode.
WAV_ARGS = ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le"]


def to_wav(mp3: Path) -> Path:
    wav = mp3.with_suffix(".wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), *WAV_ARGS, str(wav)])
    return wav


def make_silence(seconds: float, path: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
         "-t", str(seconds), *WAV_ARGS, str(path)])


def make_jingle(kind: str, path: Path) -> None:
    """Crée le jingle en WAV : fichier personnel s'il existe dans assets/, sinon synthèse ffmpeg."""
    custom = ASSETS_DIR / f"jingle_{kind}.mp3"
    if custom.exists():
        run(["ffmpeg", "-y", "-v", "error", "-i", str(custom), *WAV_ARGS, str(path)])
        return
    expr, seconds = NEWS_JINGLE
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"aevalsrc='{expr}':s={SAMPLE_RATE}:d={seconds}",
         "-af", f"{VIOLIN_FX},afade=t=out:st={seconds - 0.4}:d=0.4",
         "-t", str(seconds), *WAV_ARGS, str(path)])   # coupe la traîne de l'écho : durée exacte


def assemble(timeline: list[tuple[Path, str | None]], meta: dict) -> list[dict]:
    """
    timeline : liste de (fichier, titre_de_chapitre_ou_None), dans l'ordre d'écoute.
    Renvoie les chapitres avec leur heure de début.
    """
    # Durées et chapitres
    chapters, t = [], 0.0
    for path, title in timeline:
        if title:
            chapters.append({"title": title, "start": round(t, 2)})
        t += duration(path)
    total = t

    # Liste pour le « concat demuxer » d'ffmpeg (chemins en / : fonctionne aussi sous Windows)
    list_file = PARTS_DIR / "list.txt"
    list_file.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p, _ in timeline),
                         encoding="utf-8")

    # Métadonnées + chapitres au format ffmetadata (les apps podcast les affichent)
    title = f"CyberVeille du {meta.get('date', '')}"
    lines = [";FFMETADATA1", f"title={title}", "artist=CyberVeille", "album=CyberVeille", "genre=Podcast"]
    for i, ch in enumerate(chapters):
        end = chapters[i + 1]["start"] if i + 1 < len(chapters) else total
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(ch['start'] * 1000)}",
                  f"END={int(end * 1000)}", f"title={ch['title']}"]
    meta_file = PARTS_DIR / "metadata.txt"
    meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run(["ffmpeg", "-y", "-v", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-i", str(meta_file), "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
         # loudnorm : volume régulier, au standard podcast (-16 LUFS) — audible même dans le métro
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "libmp3lame", "-b:a", BITRATE,
         "-id3v2_version", "3", str(EPISODE_FILE)])
    return chapters


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------

def check_ffmpeg() -> bool:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    print("✗ ffmpeg introuvable. Windows : `winget install Gyan.FFmpeg` puis rouvre le terminal.",
          file=sys.stderr)
    return False


def build_jobs(script: dict) -> list[tuple[str, Path, str | None]]:
    """Liste ordonnée des passages à lire : (texte, fichier, titre de chapitre s'il commence ici)."""
    jobs, n = [], 0

    def add(text: str, chapter: str | None = None) -> None:
        nonlocal n
        n += 1
        jobs.append((clean_for_speech(text), PARTS_DIR / f"{n:03d}.mp3", chapter))

    add(script["intro"], "Introduction")
    for chapter in script["chapters"]:
        add(f"{chapter['title']}.", chapter["title"])        # la voix annonce la rubrique
        for seg in chapter["segments"]:
            add(seg["text"])
    add(script["outro"], "Conclusion")
    return jobs


def make_samples() -> int:
    """Un même extrait lu par chaque voix, pour choisir à l'oreille."""
    BUILD_DIR.mkdir(exist_ok=True)
    text = clean_for_speech(
        "Bonjour et bienvenue dans CyberVeille. Le CERT-FR alerte sur une faille critique, "
        "la CVE-2026-12345, notée CVSS 9.8, activement exploitée dans des VPN. "
        "La CISA l'a ajoutée à son catalogue KEV : mettez à jour sans attendre.")
    for voice in SAMPLE_VOICES:
        path = BUILD_DIR / f"sample_{voice.split('-')[2]}.mp3"
        asyncio.run(synthesize(text, path, voice))
        print(f"✓ {path.relative_to(ROOT)}")
    print("\nÉcoute les extraits, puis choisis ta voix avec la variable TTS_VOICE (voir le guide).")
    return 0


def main() -> int:
    if "--samples" in sys.argv:
        return make_samples()
    if not check_ffmpeg():
        return 1
    if not SCRIPT_FILE.exists():
        print("✗ build/script.json introuvable : lance d'abord generate.py", file=sys.stderr)
        return 1

    script = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))
    shutil.rmtree(PARTS_DIR, ignore_errors=True)       # repartir propre : pas de vieux morceaux mélangés
    PARTS_DIR.mkdir(parents=True)

    jobs = build_jobs(script)
    print(f"Voix : {VOICE} ({RATE}) — {len(jobs)} passages")
    try:
        asyncio.run(synthesize_all([(text, path) for text, path, _ in jobs]))
    except RuntimeError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1   # job en échec → mail d'alerte (on ne publie jamais un épisode à trous)

    # Ce qui sépare deux passages : le jingle violon UNIQUEMENT entre deux articles d'une même rubrique.
    # Changement de rubrique (l'annonce « Alertes… » suffit), intro et conclusion : simple silence.
    make_silence(SHORT_PAUSE, PARTS_DIR / "pause_courte.wav")
    make_silence(LONG_PAUSE, PARTS_DIR / "pause_longue.wav")
    after_title = [PARTS_DIR / "pause_courte.wav"]       # entre « Nom de la rubrique. » et son 1er article
    between_rubrics = [PARTS_DIR / "pause_longue.wav"]
    if JINGLES:
        gap = PARTS_DIR / "souffle.wav"
        make_silence(JINGLE_GAP, gap)
        make_jingle("news", PARTS_DIR / "jingle_news.wav")
        between_news = [gap, PARTS_DIR / "jingle_news.wav", gap]
    else:
        between_news = [PARTS_DIR / "pause_courte.wav"]

    # Ordre d'écoute : passage, séparateur, passage…
    timeline: list[tuple[Path, str | None]] = []
    for i, (_, path, chapter) in enumerate(jobs):
        if i > 0:
            previous = jobs[i - 1][2]
            if chapter:
                separator = between_rubrics
            elif previous and previous != "Introduction":   # on sort d'une annonce de rubrique
                separator = after_title
            else:
                separator = between_news
            timeline += [(p, None) for p in separator]
        timeline.append((to_wav(path), chapter))

    print("   assemblage et normalisation du volume…")
    chapters = assemble(timeline, script.get("meta", {}))
    CHAPTERS_FILE.write_text(json.dumps(chapters, indent=2, ensure_ascii=False), encoding="utf-8")

    minutes = duration(EPISODE_FILE) / 60
    size = EPISODE_FILE.stat().st_size / 1_000_000
    print(f"→ build/episode.mp3 : {minutes:.1f} min, {size:.1f} Mo, {len(chapters)} chapitres")
    job_summary(f"### Audio\n- Voix : {VOICE} ({RATE})\n- {minutes:.1f} min, {size:.1f} Mo, {len(chapters)} chapitres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
