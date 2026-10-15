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
