// Tests de docs/revision.js (révisions espacées), lancés par tests/test_app.py avec Node.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "docs", "revision.js"), "utf8"), ctx);
const R = ctx.CVRevision;
const plain = (x) => JSON.parse(JSON.stringify(x));   // objets créés dans un autre « contexte » Node

const ep = (date, n = 2) => ({ date, fiche: { quiz: Array.from({ length: n }, (_, i) => ({ question: `Q${i} ${date} ?`, reponse: `R${i}` })) } });
const episodes = [ep("2026-10-15"), ep("2026-10-14"), ep("2026-10-13", 1), { date: "2026-10-12", fiche: null }];

// Cartes : une par question, rien pour un épisode sans fiche
assert.equal(R.cardsFrom(episodes).length, 5);

// Le jour même de l'épisode, ses questions ne sont pas encore à réviser (on vient de les entendre)
let due = R.dueCards(episodes, {}, "2026-10-15");
assert.deepEqual(plain(due.map((c) => c.id)), ["2026-10-14#0", "2026-10-14#1", "2026-10-13#0"]);

// « Je savais » : boîte 2 → revient dans 3 jours ; « À revoir » : boîte 1 → revient demain
let progress = R.grade({}, "2026-10-14#0", true, "2026-10-15");
assert.deepEqual(plain(progress["2026-10-14#0"]), { box: 2, due: "2026-10-18" });
progress = R.grade(progress, "2026-10-14#1", false, "2026-10-15");
assert.equal(progress["2026-10-14#1"].due, "2026-10-16");
progress = R.grade(progress, "2026-10-14#0", true, "2026-10-18");
assert.equal(progress["2026-10-14#0"].due, "2026-10-25");                 // boîte 3 : une semaine

// Une carte révisée ne revient qu'à sa date ; les cartes en retard passent en premier
due = R.dueCards(episodes, progress, "2026-10-16");
assert.equal(due[0].id, "2026-10-14#1");
assert.ok(!due.some((c) => c.id === "2026-10-14#0"));

// Séance limitée, et ménage des cartes d'épisodes disparus
assert.equal(R.dueCards(episodes, {}, "2026-10-20", 2).length, 2);
assert.deepEqual(plain(Object.keys(R.prune({ "2026-09-01#0": { box: 1, due: "x" }, "2026-10-14#0": { box: 1, due: "x" } }, episodes))),
                 ["2026-10-14#0"]);
assert.equal(R.addDays("2026-10-31", 1), "2026-11-01");
console.log("revision.js : OK");
