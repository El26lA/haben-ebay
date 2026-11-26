// Meldestelle für eBays „Marketplace Account Deletion“ als Cloudflare Worker.
//
// Damit muss zu Hause kein Port offen sein: Der Worker beantwortet die Prüfung von eBay und legt jede Meldung
// in einer D1-Datenbank ab. haben-ebay holt die Meldungen bei jedem Lauf ab (Verbindung nach außen) und lässt
// sie danach hier löschen. Gespeichert wird dauerhaft nur bei haben-ebay.
//
// eBay schickt die Löschungen aller eBay-Konten weltweit, über 1.000 am Tag. Deshalb D1 (kostenlos 100.000
// geschriebene Zeilen am Tag) und nicht KV (kostenlos nur 1.000 Schreibvorgänge am Tag).
//
// Einrichten (Anleitung in README.md, „Meldestelle bei Cloudflare“):
//   D1-Datenbank an den Worker binden, Variablenname DB (die Tabelle legt der Worker selbst an)
//   Geheimnisse (Secrets) des Workers, die Werte zeigt haben-ebay unter „Kontolöschungen“:
//     VERIFICATION_TOKEN  dasselbe Token wie bei eBay
//     ENDPOINT            genau die Adresse, die bei eBay steht (…/ebay/konto-loeschung)
//     ABHOL_SCHLUESSEL    Schlüssel, mit dem haben-ebay abholt

const PFAD = "/ebay/konto-loeschung";
const ABHOLEN = "/abholen";
const MAX_BYTES = 65536;
const MAX_JE_ABRUF = 500;
// Holt haben-ebay lange nichts ab (NAS aus, Dienst entfernt), räumt der Worker selbst auf, damit die Datenbank
// nie vollläuft (kostenlos höchstens 500 MB, bei ~1 MB am Tag reicht ein Jahr sicher).
const AUFBEWAHREN_TAGE = 365;
const AUFRAEUMEN_JEDE = 1000; // nur jede 1000. Meldung prüft das Alter, das spart Lesevorgänge
let tabelleDa = false;

const json = (status, daten) =>
  new Response(daten === undefined ? null : JSON.stringify(daten), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

async function tabelle(env) {
  if (!tabelleDa) {
    await env.DB.prepare("CREATE TABLE IF NOT EXISTS meldungen (id INTEGER PRIMARY KEY AUTOINCREMENT, eingang TEXT NOT NULL, nachricht TEXT NOT NULL)").run();
    tabelleDa = true;
  }
}

async function sha256hex(text) {
  const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function schluesselOk(request, env) {
  const soll = env.ABHOL_SCHLUESSEL || "";
  const ist = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
  if (soll.length < 32) return false;
  const a = new TextEncoder().encode(ist);
  const b = new TextEncoder().encode(soll);
  return a.byteLength === b.byteLength && crypto.subtle.timingSafeEqual(a, b);
}

async function meldungAnnehmen(request, env) {
  if (Number(request.headers.get("Content-Length") || 0) > MAX_BYTES) return json(400);
  const text = await request.text();
  if (!text || text.length > MAX_BYTES) return json(400);
  let nachricht;
  try {
    nachricht = JSON.parse(text);
  } catch {
    return json(400);
  }
  // Nur Kontolöschungen ablegen; alles andere bestätigen und verwerfen
  if (nachricht?.metadata?.topic !== "MARKETPLACE_ACCOUNT_DELETION") return json(204);
  await tabelle(env);
  const neu = await env.DB.prepare("INSERT INTO meldungen (eingang, nachricht) VALUES (?, ?)").bind(new Date().toISOString(), text).run();
  if (neu.meta.last_row_id % AUFRAEUMEN_JEDE === 0) {
    const grenze = new Date(Date.now() - AUFBEWAHREN_TAGE * 86400000).toISOString();
    await env.DB.prepare("DELETE FROM meldungen WHERE eingang < ?").bind(grenze).run();
  }
  return json(204); // eBay erwartet 200, 201, 202 oder 204, sonst wiederholt es die Meldung
}

async function abholen(env) {
  await tabelle(env);
  const { results } = await env.DB.prepare("SELECT id, nachricht FROM meldungen ORDER BY id LIMIT ?").bind(MAX_JE_ABRUF + 1).all();
  const meldungen = results.slice(0, MAX_JE_ABRUF).map((r) => ({ id: r.id, nachricht: JSON.parse(r.nachricht) }));
  return json(200, { meldungen, weitere: results.length > MAX_JE_ABRUF });
}

async function loeschen(request, env) {
  let daten;
  try {
    daten = await request.json();
  } catch {
    return json(400);
  }
  const ids = (Array.isArray(daten?.erledigt) ? daten.erledigt : []).filter(Number.isInteger).slice(0, MAX_JE_ABRUF);
  await tabelle(env);
  const befehle = [];
  for (let i = 0; i < ids.length; i += 90) {
    const teil = ids.slice(i, i + 90); // D1 erlaubt höchstens 100 Parameter je Abfrage
    befehle.push(env.DB.prepare(`DELETE FROM meldungen WHERE id IN (${teil.map(() => "?").join(",")})`).bind(...teil));
  }
  if (befehle.length) await env.DB.batch(befehle);
  return json(200, { geloescht: ids.length });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === PFAD && request.method === "GET") {
      const code = url.searchParams.get("challenge_code");
      if (!code || !env.VERIFICATION_TOKEN || !env.ENDPOINT) return json(400, { error: "challenge_code fehlt oder Meldestelle nicht eingerichtet" });
      return json(200, { challengeResponse: await sha256hex(code + env.VERIFICATION_TOKEN + env.ENDPOINT) });
    }
    if (url.pathname === PFAD && request.method === "POST") return meldungAnnehmen(request, env);
    if (url.pathname === ABHOLEN) {
      if (!schluesselOk(request, env)) return json(404);
      if (request.method === "GET") return abholen(env);
      if (request.method === "POST") return loeschen(request, env);
    }
    return json(404);
  },
};
