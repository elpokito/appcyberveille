"""Tests de langue.py : repérer (et corriger) les phrases que la voix lirait en anglais."""
import langue

# La vraie phrase de l'épisode du 15/10/2026, lue entièrement avec l'accent anglais.
ATLASSIAN = ("Une vulnérabilité, identifiée sous le numéro CVE-2026-21589, touche plusieurs produits "
             "Atlassian : Bitbucket Data Center, Confluence Data Center, Jira Service Management Data Center, "
             "Jira Software Data Center, Bamboo Data Center, Crowd Data Center, Crucible et Fisheye.")


def test_liste_de_produits_reperee():
    assert langue.is_english_heavy(ATLASSIAN)


def test_titre_anglais_recopie_repere():
    assert langue.is_english_heavy("Attacker Chains Three Ivanti Connect Secure Zero-Days for Root Access.")


def test_phrases_francaises_normales_non_reperees():
    for phrase in ["Microsoft a corrigé un nombre record de 1 413 failles, dont une faille de Windows Shell.",
                   "Le groupe Silent Lynx exploite une faille de WinRAR pour déployer un reverse shell.",
                   "La CISA a ajouté cette faille à son catalogue KEV : mettez à jour sans attendre."]:
        assert not langue.is_english_heavy(phrase), phrase


def test_liste_raccourcie_et_debut_de_phrase_conserve():
    texte, corrigees = langue.soften_english("Premier point. " + ATLASSIAN + " Mettez à jour.")
    assert corrigees == 1
    assert texte.startswith("Premier point. Une vulnérabilité, identifiée sous le numéro CVE-2026-21589")
    assert "Bitbucket Data Center, Confluence Data Center et plusieurs autres." in texte
    assert "Crowd" not in texte and texte.endswith("Mettez à jour.")
    assert not langue.english_heavy_sentences(texte)


def test_texte_normal_jamais_modifie():
    texte = "La faille touche Adobe Acrobat et Photoshop.  Deux espaces ici, et c'est voulu."
    assert langue.soften_english(texte) == (texte, 0)
