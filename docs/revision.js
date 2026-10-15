/*
 * CyberVeille — révisions espacées (méthode des boîtes de Leitner), sans dépendance.
 *
 * Pourquoi : on oublie vite ce qu'on n'a entendu qu'une fois. Se re-tester à intervalles
 * croissants (1 jour, 3 jours, une semaine…) est la méthode la plus efficace pour retenir
 * à long terme (effet d'espacement + effet de test) : c'est le principe d'Anki.
 *
 * Chaque question du quiz quotidien devient une carte. Si on savait la réponse, la carte monte
 * d'une boîte et revient plus tard ; sinon elle redescend en boîte 1 et revient dès demain.
 * La progression reste sur l'appareil (localStorage), aucun compte, rien n'est envoyé.
 *
 * Ce fichier ne contient que de la logique (aucun accès à la page) : il est testé avec Node.
 */
"use strict";

(function (root) {
  const INTERVALS = [1, 3, 7, 14, 30];   // jours avant la prochaine révision, selon la boîte (1 à 5)
  const NEW_FROM_EPISODES = 7;           // nouvelles cartes : celles des 7 derniers épisodes seulement
  const MAX_SESSION = 10;                // une séance courte, faisable dans le métro

  function todayISO(d = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;   // date locale, pas UTC
  }

  function addDays(iso, n) {
    const d = new Date(`${iso}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  /** Toutes les cartes disponibles : une par question du quiz de chaque épisode. */
  function cardsFrom(episodes) {
    const cards = [];
    for (const ep of Array.isArray(episodes) ? episodes : []) {
      const quiz = ep && ep.fiche && Array.isArray(ep.fiche.quiz) ? ep.fiche.quiz : [];
      quiz.forEach((qa, i) => {
        if (qa && qa.question && qa.reponse) {
          cards.push({ id: `${ep.date}#${i}`, date: String(ep.date), question: String(qa.question), reponse: String(qa.reponse) });
        }
      });
    }
    return cards;
  }

  /**
   * Cartes à revoir aujourd'hui : d'abord celles dont la date de révision est passée (les plus en
   * retard en premier), puis les nouvelles cartes des derniers épisodes, à partir du lendemain de
   * leur épisode (la veille, on vient de les entendre dans le quiz : réviser trop tôt sert peu).
   */
  function dueCards(episodes, progress, today, limit = MAX_SESSION) {
    const recent = new Set((Array.isArray(episodes) ? episodes : []).slice(0, NEW_FROM_EPISODES).map((e) => e.date));
    const late = [];
    const fresh = [];
    for (const card of cardsFrom(episodes)) {
      const p = progress && progress[card.id];
      if (p) {
        if (p.due <= today) late.push({ ...card, due: p.due, box: p.box });
      } else if (recent.has(card.date) && addDays(card.date, 1) <= today) {
        fresh.push({ ...card, due: null, box: 0 });
      }
    }
    late.sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : 0));
    // Nouvelles : les plus récentes d'abord, et dans l'ordre du quiz pour un même épisode.
    fresh.sort((a, b) => (a.date !== b.date ? (a.date < b.date ? 1 : -1) : a.id < b.id ? -1 : 1));
    return late.concat(fresh).slice(0, limit);
  }

  /** Enregistre une réponse : « je savais » → boîte suivante ; « à revoir » → boîte 1 (demain). */
  function grade(progress, cardId, knew, today) {
    const current = (progress && progress[cardId]) || { box: 1 };
    const box = knew ? Math.min(current.box + 1, INTERVALS.length) : 1;
    return { ...(progress || {}), [cardId]: { box, due: addDays(today, INTERVALS[box - 1]) } };
  }

  /** Oublie les cartes des épisodes qui ne sont plus dans la liste (plus de 30 jours). */
  function prune(progress, episodes) {
    const ids = new Set(cardsFrom(episodes).map((c) => c.id));
    const out = {};
    for (const [id, value] of Object.entries(progress || {})) if (ids.has(id)) out[id] = value;
    return out;
  }

  root.CVRevision = { INTERVALS, MAX_SESSION, todayISO, addDays, cardsFrom, dueCards, grade, prune };
})(typeof window !== "undefined" ? window : globalThis);
