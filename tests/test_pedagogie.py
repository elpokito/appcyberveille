"""Tests de pedagogie.py : l'expert pédagogique (vérifié comme le reste) et les rappels espacés."""
from datetime import date

import pedagogie

NEWS = [{"title": "À la une", "kind": "news", "segments": [
            {"text": "Ivanti corrige la CVE-2026-1111, exploitée activement sur ses passerelles VPN. "
                     "Les équipements exposés sur Internet sont visés en premier.", "sources": ["a"]}]},
        {"title": "Alertes et failles critiques", "kind": "news", "segments": [
            {"text": "Microsoft publie son Patch Tuesday avec une faille de Windows déjà exploitée.",
             "sources": ["b"]}]},
        {"title": "En bref", "kind": "breves", "segments": [{"text": "Une fuite touche un hôpital.", "sources": ["c"]}]}]

BONNE_REPONSE = """FIL_ROUGE: Un équipement exposé sur Internet se corrige en priorité, avant le reste du parc.
RETENIR: Ivanti corrige la CVE-2026-1111, déjà exploitée sur ses passerelles VPN.
RETENIR: Le Patch Tuesday de Microsoft corrige une faille de Windows déjà exploitée.
RETENIR: Une fuite de données touche un hôpital.
CONCEPT: Zero-day
EXPLICATION: Une faille zero-day est exploitée avant que l'éditeur ne publie un correctif. C'est comme une serrure dont les cambrioleurs ont la clé avant le serrurier. Ici, les passerelles VPN d'Ivanti en sont un exemple. Côté MITRE ATT&CK, cela relève de la tactique accès initial.
QUESTION: Quel éditeur a corrigé une faille exploitée sur ses passerelles VPN ?
REPONSE: Ivanti.
QUESTION: Que corrige-t-on en priorité ?
REPONSE: Les équipements exposés sur Internet."""


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def ask(self, messages):
        self.calls += 1
        return self.answers.pop(0)


def test_lecture_de_la_reponse_de_l_expert():
    memo = pedagogie.parse_memo(BONNE_REPONSE)
    assert memo["fil_rouge"].startswith("Un équipement exposé")
    assert len(memo["a_retenir"]) == 3 and len(memo["quiz"]) == 2
    assert memo["concept"]["nom"] == "Zero-day"
    assert not any(pedagogie.validate_memo(memo, pedagogie.script_text(NEWS)).values())


def test_cve_inventee_dans_le_quiz_refusee():
    memo = pedagogie.parse_memo(BONNE_REPONSE.replace("REPONSE: Ivanti.", "REPONSE: Ivanti, CVE-2026-9999."))
    errors = pedagogie.validate_memo(memo, pedagogie.script_text(NEWS))
    assert any("CVE-2026-9999" in e for e in errors["quiz"]) and not errors["fil_rouge"]


def test_identifiant_attack_interdit_dans_le_concept():
    memo = pedagogie.parse_memo(BONNE_REPONSE.replace("la tactique accès initial", "la technique T1190"))
    assert pedagogie.validate_memo(memo, pedagogie.script_text(NEWS))["concept"]


def test_expert_corrige_au_second_essai():
    fautive = BONNE_REPONSE.replace("REPONSE: Ivanti.", "REPONSE: Ivanti, CVE-2026-9999.")
    llm = FakeLLM(fautive, BONNE_REPONSE)
    memo, secours = pedagogie.ask_memo(llm, NEWS, [])
    assert llm.calls == 2 and secours == [] and memo["quiz"][0]["reponse"] == "Ivanti."


def test_expert_indisponible_version_de_secours():
    class Panne:
        def ask(self, messages):
            raise RuntimeError("aucun fournisseur")
    memo, secours = pedagogie.ask_memo(Panne(), NEWS, [])
    assert memo["fil_rouge"].startswith("Ivanti corrige")         # 1re phrase du sujet à la une
    assert memo["quiz"] == [] and "quiz" in secours                # jamais de question inventée par le code


def test_chapitres_de_fin_dans_l_ordre():
    memo = pedagogie.parse_memo(BONNE_REPONSE)
    chapters = pedagogie.memo_chapters(memo)
    assert [c["title"] for c in chapters] == ["Le concept du jour", "À retenir", "Quiz"]
    quiz = chapters[2]["segments"]
    assert quiz[0]["style"] == "question" and quiz[1]["text"] == "Réponse : Ivanti."


def episode(day, question):
    return {"date": day, "memo": {"fil_rouge": f"Fil du {day}.", "a_retenir": [f"Point du {day}."],
                                  "quiz": [{"question": question, "reponse": "Réponse."}]}}


def test_rappels_espaces_hier_trois_jours_une_semaine():
    episodes = [episode("2026-10-14", "Q hier ?"), episode("2026-10-12", "Q J-3 ?"),
                episode("2026-10-08", "Q J-7 ?"), episode("2026-10-13", "Q J-2 ?")]
    items = pedagogie.review_items(episodes, date(2026, 10, 15))          # un jeudi
    assert [(i["jours"], i["question"]) for i in items] == [(1, "Q hier ?"), (3, "Q J-3 ?"), (7, "Q J-7 ?")]
    chapter = pedagogie.review_chapter(items)
    assert chapter["segments"][0]["text"] == "La question d'hier : Q hier ?"
    assert chapter["segments"][4]["text"] == "La question d'il y a une semaine : Q J-7 ?"


def test_le_dimanche_les_rappels_couvrent_la_semaine():
    episodes = [episode(f"2026-10-{d:02d}", f"Q {d} ?") for d in range(12, 18)]
    items = pedagogie.review_items(episodes, date(2026, 10, 18))          # un dimanche
    assert [i["jours"] for i in items] == [2, 4, 6]


def test_recapitulatif_du_dimanche():
    episodes = [episode(f"2026-10-{d:02d}", f"Q {d} ?") for d in range(12, 18)]
    assert pedagogie.recap_chapter(None, episodes, date(2026, 10, 16)) is None      # pas un dimanche
    recap = pedagogie.recap_chapter(None, episodes, date(2026, 10, 18))             # sans IA : version de secours
    assert recap["title"] == "La semaine en bref"
    assert recap["segments"][0]["text"].startswith("Lundi : Fil du 2026-10-12.")
    texte = ("Cette semaine, " + "les correctifs se sont enchaînés et les attaques ont visé les équipements exposés. " * 6)
    recap = pedagogie.recap_chapter(FakeLLM(texte), episodes, date(2026, 10, 18))
    assert recap["segments"][0]["text"].startswith("Cette semaine")


def test_concepts_deja_vus():
    episodes = [{"memo": {"concept": {"nom": "SSRF"}}}, {"memo": None}, {}]
    assert pedagogie.seen_concepts(episodes) == ["SSRF"]
