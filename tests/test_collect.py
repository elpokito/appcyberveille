"""Tests de collect.py : nettoyage, empreintes, dédoublonnage (aucun accès réseau)."""
import collect


def item(id_, title, priority=2, published="2026-10-15T10:00:00+00:00"):
    return {"id": id_, "title": title, "priority": priority, "published": published, "summary": "", "source": "x"}


def test_normalize_url_retire_le_pistage():
    a = collect.normalize_url("https://Ex.com/article/?utm_source=x&id=3#haut")
    b = collect.normalize_url("https://ex.com/article?id=3")
    assert a == b


def test_clean_text_retire_le_html_et_decode():
    assert collect.clean_text("<p>Faille &amp; correctif</p>\n\n critique") == "Faille & correctif critique"


def test_dedoublonnage_garde_la_source_prioritaire():
    items = [item("a", "Faille critique dans FortiOS", priority=1),
             item("b", "Faille critique dans FortiOS !", priority=3)]
    kept = collect.deduplicate(items, seen={})
    assert [i["id"] for i in kept] == ["b"]


def test_un_sujet_deja_traite_bloque_aussi_ses_doublons():
    items = [item("a", "Faille critique dans FortiOS"), item("b", "Faille critique dans FortiOS !")]
    assert collect.deduplicate(items, seen={"a": "2026-10-14"}, today="2026-10-15") == []


def test_relance_le_meme_jour_reprend_les_news_du_matin():
    # Bug du 15/10/2026 : la relance du soir n'avait plus que 4 news → épisode de 3 min.
    items = [item("a", "Faille critique dans FortiOS"), item("b", "Fuite de données chez Acme")]
    kept = collect.deduplicate(items, seen={"a": "2026-10-15", "b": "2026-10-14"}, today="2026-10-15")
    assert [i["id"] for i in kept] == ["a"]
