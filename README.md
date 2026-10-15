# CyberVeille

Podcast quotidien de veille cybersécurité, en français, entièrement automatisé et gratuit.
Chaque nuit, un pipeline collecte les news cyber, en fait un script radio avec un LLM,
le lit avec une voix de synthèse et le publie dans une app installable sur iPhone (PWA)
et dans un flux podcast.

**App :** https://elpokito.github.io/appcyberveille/ · **Flux podcast :** https://elpokito.github.io/appcyberveille/feed.xml

## Comment ça marche

```
GitHub Actions, chaque nuit à 3 h (heure de Paris)
  1. collect.py   CERT-FR, CISA KEV, BleepingComputer, The Hacker News, Krebs, Zataz → data/items.json
  2. generate.py  « rédacteur en chef » (regroupe, sélectionne) puis rédaction par rubriques → build/script.json
  3. tts.py       voix edge-tts, pauses, chapitres, volume -16 LUFS → build/episode.mp3
  4. publish.py   MP3 en GitHub Release, flux RSS + liste des épisodes dans docs/
  5. deploy-site  app + flux + 7 derniers MP3 sur GitHub Pages (écoute hors ligne)
```

| Dossier | Contenu |
| --- | --- |
| `src/` | Les 4 étapes du pipeline + `common.py` |
| `docs/` | L'app (PWA) : `index.html`, `app.js`, `sw.js` (hors ligne), icônes |
| `tests/` | Tests automatiques, sans réseau ni clé API |
| `data/` | Mémoire du pipeline : `seen.json` (news déjà traitées), `episodes.json` |
| `.github/workflows/` | Épisode quotidien, mise en ligne du site, tests |

## Mise en place

1. **Secrets** (*Settings → Secrets and variables → Actions*) : `GROQ_API_KEY`, `OPENROUTER_API_KEY`
   (secours), et en option `NTFY_TOPIC` pour une alerte sur le téléphone en cas d'échec.
2. **GitHub Pages** (*Settings → Pages*) : source **GitHub Actions**.
3. **Premier épisode** : onglet *Actions → Épisode quotidien → Run workflow*.

## Lancer en local (Windows)

```bash
py -m pip install -r requirements.txt -r requirements-dev.txt
py src/collect.py
py src/generate.py          # clés lues dans un fichier .env (jamais commité)
py src/tts.py               # nécessite ffmpeg : winget install Gyan.FFmpeg
py src/publish.py --dry-run # aperçu du flux dans build/site_preview/
py -m pytest -q tests
```

## Sécurité

- Clés API uniquement dans GitHub Secrets ou un `.env` ignoré par Git ; push protection et secret scanning activés.
- Workflows en moindre privilège : chaque job déclare ses seules permissions.
- Anti-hallucination : chaque CVE citée doit exister dans les sources, sinon le passage est réécrit ou écarté.
- Anti-injection de prompt : les articles sont transmis au LLM comme données délimitées, jamais comme instructions.
- App : CSP stricte, aucun HTML venant des flux (protection XSS), aucun cookie ni traceur.

## Avertissement

Les résumés sont générés automatiquement par IA à partir de sources publiques et peuvent contenir des erreurs.
Vérifiez toujours l'information à la source, dont le lien accompagne chaque épisode.

## Licence

[AGPL-3.0](LICENSE)
