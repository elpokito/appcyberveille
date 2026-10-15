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
    assert softened == 1 and "plusieurs autres" in jobs[2][0]
