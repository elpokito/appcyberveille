"""
langue.py — Garder la voix en français.

Pourquoi ce fichier existe : la voix « Vivienne Multilingual » devine la langue de CHAQUE PHRASE.
Si une phrase contient surtout des mots anglais (typiquement une liste de produits :
« Bitbucket Data Center, Confluence Data Center, Jira Service Management Data Center… »),
elle bascule et lit TOUTE la phrase avec la prononciation anglaise, même les mots français.
C'est exactement ce qui s'est produit dans l'épisode du 15/10/2026.

Ce module sert à deux endroits :
  - generate.py : repère ces phrases et demande au LLM de les reformuler ;
  - tts.py      : dernier filet de sécurité, raccourcit les listes de noms juste avant la lecture.
"""

import re

# Mots français très courants (sans accent : ceux avec accent sont reconnus automatiquement).
FRENCH_WORDS = set("""
le la les l un une des du de d au aux et ou mais donc car ni que qu qui quoi dont ce c cet cette ces
se s sa son ses leur leurs lui il elle ils elles on nous vous est sont a ont avait avoir fait font
peut peuvent doit doivent va vont dans sur sous pour par avec sans entre vers chez plus moins aussi
encore ne n pas jamais rien tout tous toute toutes plusieurs selon depuis avant pendant contre lors
afin ainsi cela ceci y en si comme quand alors notamment autres autre faille failles produit produits
version versions mise jour correctif correctifs groupe groupes attaquants pirates utilisateurs
entreprise entreprises logiciel logiciels serveur serveurs touche touchent permet permettent concerne
concernent sujet aujourd hui
""".split())

# Mots anglais fréquents dans les titres et les noms de produits.
# (« on », « a », « en »… sont aussi français : ils n'y figurent volontairement pas.)
ENGLISH_WORDS = set("""
the and of to in for with at by from is are was were be been has have had it its this that these those
your you our their which who what when how not no new data center centre server cloud software service
services management manager gateway secure email connect products product multiple remote code execution
flaw flaws vulnerability vulnerabilities exploited exploits attack attacks attackers warns warning patch
patches patched fixes fixed update updates critical users user access enterprise edition suite platform
network networks tool tools endpoint detection response security protection advanced
""".split())

FRENCH_ACCENTS = set("éèêëàâäîïôöûùüçœ")
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9\-\.+]*")
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý«])")

# Une phrase est « à risque » si au moins 4 mots anglais ou noms propres y dominent les mots français.
MIN_FOREIGN = 4


def split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_RE.split(text.strip()) if s]


def word_counts(sentence: str) -> tuple[int, int]:
    """(mots français, mots anglais ou noms propres) d'une phrase."""
    french = foreign = 0
    tokens = TOKEN_RE.findall(sentence.replace("’", "'").replace("'", " "))
    for n, token in enumerate(tokens):
        low = token.lower().strip(".-")
        if any(c in FRENCH_ACCENTS for c in low) or low in FRENCH_WORDS:
            french += 1
        elif low in ENGLISH_WORDS:
            foreign += 1
        elif any(c.isdigit() for c in low):
            continue                                   # CVE-2026-1234, 9.8… : neutre
        elif token[0].isupper() and n > 0:
            foreign += 1                               # nom propre (Bitbucket, Jira…)
        else:
            french += 1                                # mot en minuscules sans accent : le plus souvent français
    return french, foreign


def is_english_heavy(sentence: str) -> bool:
    french, foreign = word_counts(sentence)
    return foreign >= MIN_FOREIGN and foreign > french


def english_heavy_sentences(text: str) -> list[str]:
    return [s for s in split_sentences(text) if is_english_heavy(s)]


# --------------------------------------------------------------------------
# Raccourcir une liste de noms : « dans Adobe, Xen, Zabbix et Juniper Networks »
#                             → « dans Adobe, Xen et plusieurs autres »
# --------------------------------------------------------------------------

def _is_name_token(token: str) -> bool:
    if any(c in FRENCH_ACCENTS for c in token.lower()):
        return False
    return any(c.isupper() or c.isdigit() for c in token) or token.lower() in ENGLISH_WORDS


def _is_name(part: str) -> bool:
    tokens = TOKEN_RE.findall(part)
    return bool(tokens) and all(_is_name_token(t) for t in tokens)


def _split_trailing_name(part: str) -> tuple[str, str]:
    """« touche les produits Atlassian : Bitbucket Data Center » → (« … Atlassian : », « Bitbucket Data Center »).
    On remonte les mots depuis la fin tant que ce sont des noms, sans franchir une ponctuation."""
    tokens = list(TOKEN_RE.finditer(part))
    start = None
    for i in range(len(tokens) - 1, -1, -1):
        gap = part[tokens[i].end():tokens[i + 1].start()] if i + 1 < len(tokens) else part[tokens[i].end():]
        if gap.strip() or not _is_name_token(tokens[i].group()):
            break
        start = tokens[i].start()
    return (part, "") if start is None else (part[:start], part[start:].strip())


def _shorten_list(sentence: str, keep: int) -> str:
    end = re.search(r"\s*[.!?…]*$", sentence).group(0)
    body = sentence[:len(sentence) - len(end)]
    parts = body.split(", ")
    # « …, C et D » : si C et D sont deux noms, ce sont deux éléments de la liste
    last = re.split(r"\s+(?:et|ou)\s+", parts[-1])
    if len(last) == 2 and _is_name(last[1]) and _is_name(_split_trailing_name(last[0])[1] or "-"):
        parts = parts[:-1] + last
        final_pair = len(parts) - 2                   # index de C : « C et D » forment la fin de liste
    else:
        final_pair = None
    for first in range(len(parts) - 2):
        prefix, name = _split_trailing_name(parts[first])
        if not name:
            continue
        run = [name]
        for nxt in parts[first + 1:]:
            if not _is_name(nxt):
                break
            run.append(nxt)
        if len(run) < max(3, keep + 1):
            continue
        after = first + len(run)
        if final_pair is not None and after <= final_pair:
            continue                                   # la liste trouvée n'est pas celle de la fin : on ne touche à rien
        shortened = prefix + ", ".join(run[:keep]) + " et plusieurs autres"
        return ", ".join(parts[:first] + [shortened] + parts[after:]) + end
    return sentence


def soften_english(text: str) -> tuple[str, int]:
    """Raccourcit les listes de noms dans les phrases à risque. Renvoie (texte, nb de phrases corrigées).
    Une phrase normale n'est jamais modifiée."""
    out, fixed = [], 0
    for sentence in split_sentences(text):
        original = sentence
        if is_english_heavy(sentence):
            for keep in (2, 1):                         # d'abord 2 noms, puis 1 si ça ne suffit pas
                sentence = _shorten_list(original, keep)
                if not is_english_heavy(sentence):
                    break
        fixed += sentence != original
        out.append(sentence)
    return (" ".join(out) if fixed else text), fixed
