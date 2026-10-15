/*
 * CyberVeille — logique de l'app (JavaScript sans framework, aucune dépendance externe).
 *
 * Sécurité : les titres des news viennent de sites tiers (flux RSS). On ne les insère JAMAIS
 * comme HTML (innerHTML) mais toujours comme texte (textContent) : un titre piégé contenant
 * <script> s'affiche comme du texte au lieu de s'exécuter (protection XSS). Les liens sont
 * acceptés seulement en http(s) pour bloquer les liens « javascript: ».
 */
"use strict";

const AUDIO_CACHE = "cv-audio";       // même nom que dans sw.js
const SPEEDS = [1, 1.15, 1.3, 1.5, 1.75, 2];

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const state = { episodes: [], current: null, chapters: [], cached: new Set(), downloading: new Map(), openFiches: new Set() };
const CARDS_KEY = "cv:cards";              // progression des révisions (boîtes de Leitner), sur l'appareil

// ---------- Stockage local (progression, réglages) : peut être indisponible, on ne plante jamais ----------
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* stockage plein ou bloqué */ } },
};

// ---------- Utilitaires ----------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined) node.append(c);   // append(texte) = texte, pas HTML
  return node;
}
function safeUrl(u) {
  try { const p = new URL(u); return p.protocol === "https:" || p.protocol === "http:" ? p.href : null; } catch { return null; }
}
function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
function fmtDate(iso) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}
const absUrl = (path) => new URL(path, location.href).href;
const posKey = (ep) => `cv:pos:${ep.guid}`;
const doneKey = (ep) => `cv:done:${ep.guid}`;

// ---------- Hors ligne : téléchargement des épisodes dans le cache ----------
async function refreshCachedList() {
  state.cached.clear();
  if (!("caches" in window)) return;
  const cache = await caches.open(AUDIO_CACHE);
  for (const req of await cache.keys()) state.cached.add(req.url);
}
const isCached = (ep) => ep.audio && state.cached.has(absUrl(ep.audio));

async function download(ep) {
  if (!ep.audio || state.downloading.has(ep.guid)) return;
  state.downloading.set(ep.guid, 0);
  render();
  try {
    const resp = await fetch(ep.audio, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const total = Number(resp.headers.get("Content-Length")) || ep.bytes || 0;
    const reader = resp.body.getReader();
    const parts = []; let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value); got += value.length;
      if (total) { state.downloading.set(ep.guid, Math.round((got / total) * 100)); renderProgress(ep); }
    }
    const blob = new Blob(parts, { type: "audio/mpeg" });
    fetch(ep.chapters).catch(() => {});   // met aussi les chapitres en cache pour l'écoute hors ligne
    const cache = await caches.open(AUDIO_CACHE);
    await cache.put(absUrl(ep.audio), new Response(blob, { headers: { "Content-Type": "audio/mpeg", "Content-Length": String(blob.size) } }));
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist();   // demande à iOS de ne pas l'effacer
  } catch (err) {
    alertStatus(`Téléchargement impossible (${err.message}). Réessayez avec du réseau.`);
  } finally {
    state.downloading.delete(ep.guid);
    await refreshCachedList();
    render();
    showStorage();
  }
}

async function removeDownload(ep) {
  const cache = await caches.open(AUDIO_CACHE);
  await cache.delete(absUrl(ep.audio));
  await refreshCachedList();
  render();
  showStorage();
}

async function cleanupOldDownloads() {
  // Supprime les audios qui ne sont plus dans la liste (épisodes de plus de 7 jours).
  const wanted = new Set(state.episodes.filter((e) => e.audio).map((e) => absUrl(e.audio)));
  const cache = await caches.open(AUDIO_CACHE);
  for (const req of await cache.keys()) if (!wanted.has(req.url)) await cache.delete(req);
}

async function showStorage() {
  if (!navigator.storage || !navigator.storage.estimate) return;
  const { usage = 0 } = await navigator.storage.estimate();
  $("storage-info").textContent = `${state.cached.size} épisode(s) hors ligne · ${(usage / 1e6).toFixed(0)} Mo utilisés`;
}

// ---------- Affichage de la liste ----------
function alertStatus(text) { $("status").textContent = text; $("status").hidden = false; }

function renderProgress(ep) {
  const btn = document.querySelector(`[data-dl="${CSS.escape(ep.guid)}"]`);
  if (btn) btn.textContent = `Téléchargement ${state.downloading.get(ep.guid) || 0} %`;
}

function progressBar(pct) {
  // Largeur réglée via .style (CSSOM) : un attribut style="" serait bloqué par la politique de sécurité (CSP).
  const fill = el("span");
  fill.style.width = `${pct}%`;
  return el("div", { class: "progress" }, fill);
}

// ---------- Fiche du jour : fil rouge, à retenir, concept, quiz ----------
// Relire après avoir écouté = deux portes d'entrée pour la même information (on retient mieux).
function ficheBlock(ep) {
  const f = ep.fiche;
  if (!f || !f.fil_rouge) return null;
  const box = el("details", { class: "fiche", ontoggle: () => {
    if (box.open) state.openFiches.add(ep.guid); else state.openFiches.delete(ep.guid);
  } }, el("summary", {}, "Fiche du jour : à retenir et quiz"));
  box.open = state.openFiches.has(ep.guid);
  box.append(el("p", { class: "fil-rouge" }, el("strong", {}, "Fil rouge"), String(f.fil_rouge)));
  if (Array.isArray(f.a_retenir) && f.a_retenir.length) {
    box.append(el("h3", {}, "À retenir"), el("ol", {}, ...f.a_retenir.map((t) => el("li", {}, String(t)))));
  }
  if (f.concept && f.concept.nom) {
    box.append(el("h3", {}, `Le concept du jour : ${f.concept.nom}`), el("p", {}, String(f.concept.explication || "")));
  }
  if (Array.isArray(f.quiz) && f.quiz.length) {
    box.append(el("h3", {}, "Quiz"));
    for (const qa of f.quiz) {
      const answer = el("p", { class: "answer", hidden: true }, String(qa.reponse));
      const show = el("button", { type: "button", onclick: () => { answer.hidden = false; show.hidden = true; } },
        "Voir la réponse");
      box.append(el("div", { class: "qa" }, el("p", {}, String(qa.question)), answer, show));
    }
  }
  return box;
}

function render() {
  const list = $("episodes");
  list.replaceChildren();
  const online = navigator.onLine;
  for (const ep of state.episodes) {
    const pos = store.get(posKey(ep), 0);
    const done = store.get(doneKey(ep), false);
    const cached = isCached(ep);
    const playable = cached || online;
    const pct = done ? 100 : Math.min(100, Math.round((pos / (ep.seconds || 1)) * 100));

    const badges = el("div", {},
      cached ? el("span", { class: "badge ok" }, "Hors ligne ✓") : null, " ",
      done ? el("span", { class: "badge" }, "Écouté") : null);

    const actions = el("div", { class: "ep-actions" },
      el("button", { type: "button", class: "primary", disabled: !playable, onclick: () => play(ep) },
        !playable ? "Indisponible hors ligne" : state.current === ep ? "En cours" : pos > 5 && !done ? `Reprendre à ${fmtTime(pos)}` : "Écouter"));
    if (ep.audio && !cached) {
      const dl = state.downloading.has(ep.guid);
      actions.append(el("button", { type: "button", "data-dl": ep.guid, disabled: dl || !online, onclick: () => download(ep) },
        dl ? `Téléchargement ${state.downloading.get(ep.guid)} %` : "Télécharger"));
    }
    if (cached) actions.append(el("button", { type: "button", onclick: () => removeDownload(ep) }, "Supprimer"));

    const sources = el("details", { class: "sources" }, el("summary", {}, "Sources de l'épisode"));
    for (const block of ep.sources || []) {
      const ul = el("ul");
      for (const link of block.links || []) {
        const href = safeUrl(link.url);
        ul.append(el("li", {},
          href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, link.title) : link.title,
          el("span", { class: "src-name" }, ` — ${link.source}`)));
      }
      sources.append(el("h3", {}, block.rubric), ul);
    }

    list.append(el("li", { class: `ep${state.current === ep ? " current" : ""}` },
      el("div", { class: "ep-head" },
        el("div", {}, el("p", { class: "ep-title" }, fmtDate(ep.date)),
                      el("p", { class: "ep-meta" }, `${Math.round(ep.seconds / 60)} min · ${(ep.bytes / 1e6).toFixed(0)} Mo`)),
        badges),
      progressBar(pct),
      actions,
      ficheBlock(ep),
      (ep.sources || []).length ? sources : null));
  }
  $("offline-banner").hidden = online;
}

// ---------- Lecteur ----------
async function play(ep) {
  if (state.current !== ep) {
    state.current = ep;
    state.ready = false;   // pas de sauvegarde de position tant que la reprise n'est pas appliquée
    // Audio en cache (ou copie GitHub Pages) si disponible, sinon lecture en ligne depuis la Release.
    audio.src = ep.audio && (isCached(ep) || navigator.onLine) ? ep.audio : ep.stream;
    audio.playbackRate = store.get("cv:speed", 1);
    const resume = store.get(posKey(ep), 0);
    audio.addEventListener("loadedmetadata", () => {
      if (resume > 5 && resume < (audio.duration || ep.seconds) - 10) audio.currentTime = resume;
      state.ready = true;
    }, { once: true });
    $("player").hidden = false;
    $("player-title").textContent = `CyberVeille — ${fmtDate(ep.date)}`;
    state.chapters = [];
    loadChapters(ep);
    setupMediaSession(ep);
  }
  try { await audio.play(); } catch { /* lecture bloquée tant que l'utilisateur n'a pas touché l'écran */ }
  render();
}

async function loadChapters(ep) {
  try {
    const resp = await fetch(ep.chapters);
    const data = await resp.json();
    state.chapters = (data.chapters || []).map((c) => ({ start: Number(c.startTime) || 0, title: String(c.title || "") }));
  } catch { state.chapters = []; }
  const list = $("chapter-list");
  list.replaceChildren(...state.chapters.map((c, i) =>
    el("li", {}, el("button", { type: "button", "data-ch": i, onclick: () => { audio.currentTime = c.start; audio.play(); } },
      el("span", {}, c.title), el("span", {}, fmtTime(c.start))))));
  $("btn-chapters").hidden = state.chapters.length === 0;
}

function currentChapterIndex() {
  let idx = -1;
  state.chapters.forEach((c, i) => { if (audio.currentTime >= c.start - 0.25) idx = i; });
  return idx;
}

let lastSave = 0;
audio.addEventListener("timeupdate", () => {
  const ep = state.current; if (!ep) return;
  const d = audio.duration || ep.seconds;
  $("seek").max = Math.floor(d); $("seek").value = Math.floor(audio.currentTime);
  $("t-cur").textContent = fmtTime(audio.currentTime);
  $("t-left").textContent = `-${fmtTime(d - audio.currentTime)}`;
  const idx = currentChapterIndex();
  $("player-chapter").textContent = idx >= 0 ? state.chapters[idx].title : "";
  document.querySelectorAll("#chapter-list button").forEach((b) => b.classList.toggle("active", Number(b.dataset.ch) === idx));
  if (state.ready && Date.now() - lastSave > 5000) {           // on sauvegarde la position toutes les 5 s
    lastSave = Date.now();
    store.set(posKey(ep), audio.currentTime);
    if (audio.currentTime > d * 0.95) store.set(doneKey(ep), true);
  }
});
audio.addEventListener("ended", () => { if (state.current) { store.set(doneKey(state.current), true); store.set(posKey(state.current), 0); render(); } });
audio.addEventListener("play", () => { $("btn-play").textContent = "❚❚"; $("btn-play").setAttribute("aria-label", "Pause"); });
audio.addEventListener("pause", () => {
  $("btn-play").textContent = "▶"; $("btn-play").setAttribute("aria-label", "Lecture");
  if (state.current && state.ready) store.set(posKey(state.current), audio.currentTime);
});
audio.addEventListener("error", () => {
  alertStatus(navigator.onLine ? "Lecture impossible pour cet épisode." : "Cet épisode n'est pas téléchargé : réessayez avec du réseau.");
});

const skip = (s) => { audio.currentTime = Math.max(0, Math.min((audio.duration || 0) - 1, audio.currentTime + s)); };
const nextChapter = () => { const n = state.chapters[currentChapterIndex() + 1]; if (n) audio.currentTime = n.start; };

$("btn-play").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
$("btn-back").addEventListener("click", () => skip(-15));
$("btn-fwd").addEventListener("click", () => skip(30));
$("btn-next").addEventListener("click", nextChapter);
$("seek").addEventListener("input", (e) => { audio.currentTime = Number(e.target.value); });
$("btn-speed").addEventListener("click", () => {
  const next = SPEEDS[(SPEEDS.indexOf(audio.playbackRate) + 1) % SPEEDS.length] || 1;
  audio.playbackRate = next; store.set("cv:speed", next); $("btn-speed").textContent = `${next}×`;
});
$("btn-chapters").addEventListener("click", () => {
  const list = $("chapter-list"); list.hidden = !list.hidden;
  $("btn-chapters").setAttribute("aria-expanded", String(!list.hidden));
});

// Commandes sur l'écran verrouillé et les écouteurs (Media Session)
function setupMediaSession(ep) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: `CyberVeille — ${fmtDate(ep.date)}`, artist: "CyberVeille", album: "Veille cyber",
    artwork: [{ src: absUrl("icons/icon-512.png"), sizes: "512x512", type: "image/png" }],
  });
  const ms = navigator.mediaSession;
  const set = (a, h) => { try { ms.setActionHandler(a, h); } catch { /* action non supportée */ } };
  set("play", () => audio.play()); set("pause", () => audio.pause());
  set("seekbackward", () => skip(-15)); set("seekforward", () => skip(30));
  set("nexttrack", nextChapter);
  set("seekto", (d) => { audio.currentTime = d.seekTime; });
}

// ---------- Révisions espacées (logique dans revision.js) ----------
const rev = { cards: [], i: 0 };

function updateReviseEntry() {
  const R = window.CVRevision;
  // Pendant une mise à jour de l'app, une ancienne page peut charger le nouveau script : on ne plante pas.
  if (!$("revise-entry") || !$("revision")) return;
  if (!R) { $("revise-entry").hidden = true; return; }
  const progress = R.prune(store.get(CARDS_KEY, {}), state.episodes);   // oublie les épisodes disparus
  store.set(CARDS_KEY, progress);
  const total = R.cardsFrom(state.episodes).length;
  const due = R.dueCards(state.episodes, progress, R.todayISO());
  $("revise-entry").hidden = total === 0 || !$("revision").hidden;
  $("btn-revise").hidden = due.length === 0;
  $("btn-revise").textContent = `Réviser · ${due.length} carte${due.length > 1 ? "s" : ""}`;
  $("revise-info").textContent = due.length
    ? "Quelques questions des épisodes passés : deux minutes pour ne pas oublier."
    : "Rien à réviser aujourd'hui ✓ Les questions reviennent après 1, 3, 7, 14 puis 30 jours.";
}

function showCard() {
  const card = rev.cards[rev.i];
  $("rev-progress").textContent = card ? `Carte ${rev.i + 1} sur ${rev.cards.length} · épisode du ${fmtDate(card.date)}` : "";
  $("rev-question").textContent = card ? card.question : "Révision terminée ✓ À demain pour la suite.";
  $("rev-answer").textContent = card ? card.reponse : "";
  $("rev-answer").hidden = true;
  $("rev-show").hidden = !card;
  $("rev-again").hidden = true;
  $("rev-good").hidden = true;
}

function startRevision() {
  const R = window.CVRevision;
  rev.cards = R.dueCards(state.episodes, store.get(CARDS_KEY, {}), R.todayISO());
  rev.i = 0;
  $("revision").hidden = false;
  $("revise-entry").hidden = true;
  showCard();
  $("revision").scrollIntoView({ behavior: "smooth", block: "start" });
}

function gradeCard(knew) {
  const R = window.CVRevision;
  const card = rev.cards[rev.i];
  if (!card) return;
  store.set(CARDS_KEY, R.grade(store.get(CARDS_KEY, {}), card.id, knew, R.todayISO()));
  rev.i += 1;
  showCard();
}

const on = (id, fn) => { const node = $(id); if (node) node.addEventListener("click", fn); };
on("btn-revise", startRevision);
on("rev-show", () => {
  $("rev-answer").hidden = false; $("rev-show").hidden = true; $("rev-again").hidden = false; $("rev-good").hidden = false;
});
on("rev-good", () => gradeCard(true));
on("rev-again", () => gradeCard(false));
on("rev-close", () => { $("revision").hidden = true; updateReviseEntry(); });

// ---------- Installation sur iPhone ----------
function installHint() {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
  if (ios && !installed && !store.get("cv:hint-dismissed", false)) $("install-hint").hidden = false;
  $("install-dismiss").addEventListener("click", () => { store.set("cv:hint-dismissed", true); $("install-hint").hidden = true; });
}

// ---------- Démarrage ----------
async function loadEpisodes() {
  try {
    const resp = await fetch("episodes.json", { cache: "no-store" });
    // 404 = aucun épisode publié pour l'instant (tout premier lancement) : ce n'est pas une erreur.
    state.episodes = resp.status === 404 ? [] : await resp.json();
    if (!Array.isArray(state.episodes)) state.episodes = [];
    $("status").hidden = state.episodes.length > 0;
    if (!state.episodes.length) alertStatus("Aucun épisode pour l'instant : le premier arrive cette nuit.");
  } catch {
    alertStatus("Impossible de charger la liste des épisodes.");
  }
}

async function start() {
  installHint();
  $("btn-speed").textContent = `${store.get("cv:speed", 1)}×`;
  const auto = $("auto-download");
  auto.checked = store.get("cv:auto", true);
  auto.addEventListener("change", () => store.set("cv:auto", auto.checked));

  if ("serviceWorker" in navigator) {
    try { await navigator.serviceWorker.register("sw.js"); } catch { /* app utilisable sans, mais pas hors ligne */ }
  }
  await loadEpisodes();
  if ("caches" in window) { await cleanupOldDownloads(); await refreshCachedList(); }
  render();
  updateReviseEntry();
  showStorage();

  // Téléchargement auto du dernier épisode : iOS ne permet pas de le faire en arrière-plan,
  // il se fait donc à l'ouverture de l'app (pensez à l'ouvrir avant de partir).
  const latest = state.episodes[0];
  if (auto.checked && navigator.onLine && latest && latest.audio && !isCached(latest)) download(latest);
}

window.addEventListener("online", render);
window.addEventListener("offline", render);
start();
