"""Tests du nettoyage de texte avant la synthèse vocale."""
import tts


def test_cve_lisible_a_l_oral():
    assert "C.V.E. 2026, 12345" in tts.clean_for_speech("la CVE-2026-12345")


def test_score_cvss_en_francais():
    assert "9 virgule 8" in tts.clean_for_speech("noté CVSS 9.8")


def test_tirets_insecables_normalises():
    assert tts.clean_for_speech("trente‑mille dépôts") == "trente-mille dépôts"


def test_pas_de_double_point_apres_un_sigle():
    assert ".." not in tts.clean_for_speech("une faille RCE.")


def test_ssml_impose_le_francais():
    class Config:
        voice, pitch, rate, volume = "fr-FR-VivienneMultilingualNeural", "+0Hz", "+5%", "+0%"
    ssml = tts._mkssml_french(Config(), "Bonjour")
    assert "<lang xml:lang='fr-FR'>" in ssml and "xml:lang='en-US'" not in ssml and "Bonjour" in ssml


def test_listes_de_noms_raccourcies_avant_lecture():
    script = {"intro": "Bonjour.", "outro": "À demain.", "chapters": [{"title": "Alertes", "segments": [
        {"text": "La faille touche Bitbucket Data Center, Confluence Data Center, Jira Software Data Center "
                 "et Bamboo Data Center."}]}]}
    jobs, softened = tts.build_jobs(script)
    assert softened == 1 and "plusieurs autres" in jobs[2]["text"]


def episode_pedagogique():
    return {"intro_segments": [{"text": "Bonjour."}, {"text": "Le fil rouge du jour : on corrige.", "style": "cle"},
                               {"text": "Au sommaire aujourd'hui : le quiz. C'est parti !"}],
            "intro": "…", "outro": "…",
            "chapters": [{"title": "Alertes", "kind": "news", "segments": [{"text": "Un."}, {"text": "Deux."}]},
                         {"title": "En bref", "kind": "breves", "segments": [{"text": "A."}, {"text": "B."}]},
                         {"title": "Quiz", "kind": "pedago", "announce": "Le quiz : répondez dans votre tête.",
                          "segments": [{"text": "Première question : quoi ?", "style": "question"},
                                       {"text": "Réponse : ça.", "style": "cle"}]}],
            "outro_segments": [{"text": "Si vous ne deviez retenir qu'une chose : on corrige.", "style": "cle"},
                               {"text": "À demain !"}]}


def test_ce_qu_on_entend_entre_deux_passages():
    jobs, _ = tts.build_jobs(episode_pedagogique())
    seps = [tts.separator(jobs[i - 1], jobs[i]) for i in range(1, len(jobs))]
    textes = [j["text"] for j in jobs]
    assert seps[textes.index("Deux.") - 1] == "news"                  # violon entre deux articles
    assert seps[textes.index("B.") - 1] == "breve"                    # simple silence entre deux brèves
    assert seps[textes.index("Réponse : ça.") - 1] == "quiz"         # temps de réponse après une question
    assert seps[textes.index("À demain !") - 1] == "key"              # silence après le fil rouge redit
    assert [j["chapter"] for j in jobs if j["chapter"]] == ["Introduction", "Alertes", "En bref", "Quiz", "Conclusion"]


def test_l_essentiel_est_lu_plus_lentement():
    jobs, _ = tts.build_jobs(episode_pedagogique())
    lent = {j["text"] for j in jobs if j["rate"] == tts.RATE_KEY}
    assert "Réponse : ça." in lent and "Le fil rouge du jour : on corrige." in lent and "Un." not in lent
