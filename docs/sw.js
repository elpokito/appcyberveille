/*
 * Service worker CyberVeille : ce qui permet à l'app de fonctionner SANS RÉSEAU (métro).
 *  - « coquille » de l'app (HTML, CSS, JS, icônes) : servie depuis le cache, mise à jour en arrière-plan
 *  - liste des épisodes et chapitres : réseau d'abord, cache si hors ligne
 *  - fichiers audio téléchargés : servis depuis le cache, y compris par morceaux (requêtes « Range »),
 *    indispensable car Safari lit l'audio morceau par morceau
 */
const VERSION = "2026-10-16-memo";               // à changer quand on modifie l'app → force la mise à jour
const SHELL = `cv-shell-${VERSION}`;
const DATA = "cv-data";
const AUDIO = "cv-audio";                   // partagé avec app.js (téléchargements)
const SHELL_FILES = ["./", "index.html", "style.css", "app.js", "revision.js", "manifest.webmanifest",
                     "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // Supprime les coquilles des anciennes versions (jamais les audios téléchargés).
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("cv-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;   // le reste : comportement normal

  if (url.pathname.includes("/audio/")) {
    event.respondWith(serveAudio(req));
  } else if (url.pathname.endsWith("episodes.json") || url.pathname.includes("/chapters/")) {
    event.respondWith(networkFirst(req));
  } else {
    event.respondWith(staleWhileRevalidate(req));
  }
});

async function serveAudio(req) {
  const cached = await caches.open(AUDIO).then((c) => c.match(req.url));
  if (!cached) return fetch(req);                       // pas téléchargé : on streame
  const blob = await cached.blob();
  const range = req.headers.get("range");
  const headers = { "Content-Type": "audio/mpeg", "Accept-Ranges": "bytes" };
  if (!range) return new Response(blob, { status: 200, headers: { ...headers, "Content-Length": String(blob.size) } });

  // Exemple : « bytes=1000-1999 » → on renvoie seulement ce morceau (code 206 « Partial Content »).
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start = m && m[1] ? Number(m[1]) : 0;
  let end = m && m[2] ? Number(m[2]) : blob.size - 1;
  if (m && !m[1] && m[2]) { start = blob.size - Number(m[2]); end = blob.size - 1; }   // « bytes=-500 »
  if (start >= blob.size || end < start) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${blob.size}` } });
  }
  end = Math.min(end, blob.size - 1);
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: { ...headers, "Content-Length": String(end - start + 1), "Content-Range": `bytes ${start}-${end}/${blob.size}` },
  });
}

async function networkFirst(req) {
  const cache = await caches.open(DATA);
  try {
    const resp = await fetch(req, { cache: "no-store" });
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch {
    return (await cache.match(req)) || new Response("[]", { headers: { "Content-Type": "application/json" } });
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(req, { ignoreSearch: true });
  const update = fetch(req).then((resp) => { if (resp.ok) cache.put(req, resp.clone()); return resp; }).catch(() => null);
  return cached || (await update) || (await cache.match("index.html"));
}
