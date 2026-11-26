// Meldestelle für eBays „Marketplace Account Deletion“ als Cloudflare Worker.
//
// Damit muss zu Hause kein Port offen sein: Der Worker beantwortet die Prüfung von eBay und legt jede Meldung
// in einem KV-Namespace ab. haben-ebay holt die Meldungen bei jedem Lauf ab (Verbindung nach außen) und lässt
// sie danach hier löschen. Gespeichert wird dauerhaft nur bei haben-ebay.
//
// Einrichten (Anleitung in README.md, „Meldestelle bei Cloudflare“):
//   KV-Namespace an den Worker binden, Variablenname MELDUNGEN
//   Geheimnisse (Secrets) des Workers, die Werte zeigt haben-ebay unter „Kontolöschungen“:
//     VERIFICATION_TOKEN  dasselbe Token wie bei eBay
//     ENDPOINT            genau die Adresse, die bei eBay steht (…/ebay/konto-loeschung)
//     ABHOL_SCHLUESSEL    Schlüssel, mit dem haben-ebay abholt

const PFAD = "/ebay/konto-loeschung";
const ABHOLEN = "/abholen";
const MAX_BYTES = 65536;
const MAX_JE_ABRUF = 500;

const json = (status, daten) =>
  new Response(daten === undefined ? null : JSON.stringify(daten), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

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
  const id = `m:${String(Date.now()).padStart(15, "0")}:${crypto.randomUUID()}`;
  await env.MELDUNGEN.put(id, text);
  return json(204); // eBay erwartet 200, 201, 202 oder 204, sonst wiederholt es die Meldung
}

async function abholen(env) {
  const liste = await env.MELDUNGEN.list({ prefix: "m:", limit: MAX_JE_ABRUF });
  const meldungen = [];
  for (const k of liste.keys) {
    const text = await env.MELDUNGEN.get(k.name);
    if (text !== null) meldungen.push({ id: k.name, nachricht: JSON.parse(text) });
  }
  return json(200, { meldungen, weitere: !liste.list_complete });
}

async function loeschen(request, env) {
  let daten;
  try {
    daten = await request.json();
  } catch {
    return json(400);
  }
  const ids = (Array.isArray(daten?.erledigt) ? daten.erledigt : []).filter((x) => typeof x === "string" && x.startsWith("m:"));
  for (const id of ids.slice(0, MAX_JE_ABRUF)) await env.MELDUNGEN.delete(id);
  return json(200, { geloescht: Math.min(ids.length, MAX_JE_ABRUF) });
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
