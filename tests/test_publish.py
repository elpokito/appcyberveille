"""Tests du flux podcast : XML valide et contenu des sources correctement échappé."""
import xml.etree.ElementTree as ET

import publish


def test_notes_echappent_le_html_des_titres():
    md = "## Alertes\n- [Faille <script>alert(1)</script>](https://ex.com/a?x=1&y=2) — CISA"
    out = publish.notes_to_html(md)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_lien_javascript_jamais_cliquable():
    out = publish.notes_to_html("- [piège](javascript:alert(1)) — X")
    assert "<a " not in out and "href" not in out   # affiché comme simple texte


def test_flux_rss_valide():
    ep = {"title": "CyberVeille du 15/10/2026", "guid": "cv-1", "pub_date": "Thu, 15 Oct 2026 03:00:00 +0000",
          "url": "https://github.com/u/r/releases/download/t/a.mp3", "bytes": 1000, "seconds": 1200,
          "chapters_path": "chapters/2026-10-15.json", "description_html": "<p>a ]]> b</p>"}
    root = ET.fromstring(publish.build_feed([ep]))
    item = root.find("channel/item")
    assert item.find("enclosure").get("type") == "audio/mpeg"
    assert item.find("guid").text == "cv-1"
