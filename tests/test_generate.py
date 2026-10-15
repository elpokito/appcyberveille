"""Tests de generate.py : vérifications anti-hallucination et plan de secours (aucun appel au LLM)."""
import generate


def news(id_, title, source="The Hacker News", priority=2, summary="", cve=None):
    n = {"id": id_, "title": title, "source": source, "priority": priority, "summary": summary,
         "published": "2026-10-15T10:00:00+00:00", "url": f"https://ex.com/{id_}"}
    if cve:
        n["cve"] = cve
    return n


def test_cve_inventee_detectee():
    chunk = [news("a", "Faille Acme", summary="CVE-2026-1111 corrigée")]
    segments = [{"text": "La CVE-2026-9999 touche Acme.", "sources": ["a"]}]
    errors = generate.validate_segments(segments, chunk)
    assert any("CVE-2026-9999" in e for e in errors)


def test_cve_presente_dans_la_source_acceptee():
    chunk = [news("a", "Faille Acme", summary="CVE-2026-1111 corrigée")]
    assert generate.validate_segments([{"text": "La CVE-2026-1111 est corrigée.", "sources": ["a"]}], chunk) == []


def test_source_inconnue_et_url_refusees():
    chunk = [news("a", "Faille Acme")]
    assert generate.validate_segments([{"text": "Texte", "sources": ["zzz"]}], chunk)
    assert generate.validate_segments([{"text": "Voir https://ex.com", "sources": ["a"]}], chunk)


def test_parse_segments():
    raw = "Préambule ignoré\n### SOURCES: a, b\nPremier passage.\n### SOURCES: c\nSecond passage."
    assert generate.parse_segments(raw) == [{"text": "Premier passage.", "sources": ["a", "b"]},
                                            {"text": "Second passage.", "sources": ["c"]}]


def test_articles_d_opinion_penalises():
    assert generate.score(news("a", "Why AI Confidence Is Not Validation")) < 0
    assert generate.score(news("b", "Cisco patches zero-day exploited in attacks")) > 20


def test_plan_de_secours_regroupe_un_meme_evenement():
    items = [news("k", "CVE-2026-1 — Ivanti Connect Secure exploitée activement", "CISA KEV", 3, cve="CVE-2026-1"),
             news("t", "Attacker Chains Ivanti Connect Secure Zero-Days for Root Access"),
             news("o", "How to Run a Deepfake Drill")]
    plan = generate.fallback_plan(items)
    assert len(plan) == 1 and {i["id"] for i in plan[0]["items"]} == {"k", "t"}
    assert plan[0]["rubric"] == generate.RUBRICS[0]


def test_duree_maximale_respectee():
    plan = [{"rubric": generate.RUBRICS[0], "items": [], "importance": 5, "suite": False} for _ in range(40)]
    kept = generate.fit_to_length(plan)
    assert sum(generate.WORDS_BY_IMPORTANCE[s["importance"]] for s in kept) <= generate.MAX_EPISODE_WORDS


def test_delai_429_lu_dans_le_message_de_groq():
    import httpx
    resp = httpx.Response(429, json={"error": {"message": "Rate limit reached ... Please try again in 14.325s."}})
    assert generate.retry_delay(resp) == 14.325


def test_budget_par_minute_attend_avant_de_deborder(monkeypatch):
    clock = {"t": 0.0}
    pauses = []
    monkeypatch.setattr(generate.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(generate.time, "sleep", lambda s: (pauses.append(s), clock.__setitem__("t", clock["t"] + s)))
    budget = generate.TokenBudget(7000)
    for _ in range(3):
        budget.wait_for(3000)
        budget.spent(3000)
    assert len(pauses) == 1          # la 3e requête attend que la minute se libère, au lieu d'un refus 429


def test_sommaire_chiffre_et_accorde():
    chapitres = [{"title": "Alertes et failles critiques", "segments": [{}, {}]},
                 {"title": "Incidents et fuites de données", "segments": [{}]},
                 {"title": "Outils, recherche et divers", "segments": [{}]}]
    phrase = generate.summary_sentence(chapitres)
    assert phrase.startswith("Au sommaire aujourd'hui : deux alertes de sécurité, un incident")
    assert "une news outils et recherche" in phrase


def test_phrase_trop_anglaise_signalee_sans_bloquer():
    chunk = [news("a", "Atlassian flaw", summary="CVE-2026-21589")]
    seg = [{"text": "Elle touche Bitbucket Data Center, Confluence Data Center, Jira Software Data Center "
                    "et Bamboo Data Center.", "sources": ["a"]}]
    assert generate.validate_segments(seg, chunk) == []      # pas une erreur bloquante…
    assert generate.style_issues(seg)                         # … mais une reformulation est demandée


def test_relance_du_jour_n_ecarte_pas_ses_propres_sujets(tmp_path, monkeypatch):
    episodes = [{"date": "2026-10-15", "sources": [{"links": [{"title": "Sujet du jour"}]}]},
                {"date": "2026-10-14", "sources": [{"links": [{"title": "Sujet d'hier"}]}]}]
    path = tmp_path / "episodes.json"
    path.write_text(__import__("json").dumps(episodes), encoding="utf-8")
    monkeypatch.setattr(generate, "EPISODES_FILE", path)
    assert generate.recent_titles(today="2026-10-15") == ["Sujet d'hier"]
