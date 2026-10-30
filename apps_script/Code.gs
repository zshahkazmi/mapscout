/**
 * mapscout directory endpoint (v2 — with outreach automation).
 *
 * Setup: open the directory spreadsheet -> Extensions -> Apps Script ->
 * replace the editor contents with this file -> set TOKEN below to your
 * own secret word -> Deploy -> New deployment (or Manage deployments ->
 * New version) -> Web app (Execute as: Me; Who has access: Anyone).
 *
 * GET  ?token=...                       -> {ok, done_keys, place_keys}
 * GET  ?token=...&action=pending&limit=60
 *      -> {ok, rows:[{place_key, name, website, postcode, domain}]}
 *         Directory businesses with a website that have NOT been handed
 *         to outreach yet and whose domain was never contacted on the
 *         main lead sheet.
 * POST {token, directory:[rows], progress:[rows]}  -> appends (deduped).
 * POST {token, outreach:[{place_key, domain, name, outcome}]}
 *      -> records the handover in the Outreach tab (the ledger that
 *         makes `pending` skip them forever).
 */
var TOKEN = "PASTE_YOUR_OWN_SECRET_TOKEN_HERE";
var DIRECTORY_SHEET = "Directory";
var PROGRESS_SHEET = "Progress";
var OUTREACH_SHEET = "Outreach";
var LEAD_SHEET_ID = "1QvNmxurmL3FbcAGR8TofSOkaZbM_7z9_63GscQB5A48";
var DIRECTORY_HEADERS = ["name","category","rating","reviews","address","postcode","town","phone","website","hours","maps_url","place_key","query","locality","niche","scraped_at"];
var PROGRESS_HEADERS = ["key","locality","niche","query","found","added","scraped_at"];
var OUTREACH_HEADERS = ["place_key","domain","name","outcome","handed_at"];

function tab_(name, headers) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(name);
  if (!ws) ws = ss.insertSheet(name);
  if (ws.getLastRow() === 0) ws.appendRow(headers);
  return ws;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function checkToken_(t) {
  if (t !== TOKEN) throw new Error("bad token");
}

function columnValues_(ws, colIndex) {
  var last = ws.getLastRow();
  if (last < 2) return [];
  return ws.getRange(2, colIndex, last - 1, 1).getValues()
    .map(function (r) { return r[0]; })
    .filter(function (v) { return v !== "" && v !== null; });
}

function domainOf_(website) {
  var u = String(website || "").toLowerCase().trim();
  u = u.replace(/^https?:\/\//, "").replace(/^www\./, "");
  return u.split("/")[0];
}

/** Domains already contacted on the main lead sheet (any terminal
 *  status in column K). Read live so the answer is never stale. */
function contactedDomains_() {
  var book = SpreadsheetApp.openById(LEAD_SHEET_ID);
  var ws = book.getSheetByName("Sheet1");
  var last = ws.getLastRow();
  var out = {};
  if (last < 2) return out;
  var sites = ws.getRange(2, 5, last - 1, 1).getValues();
  var stats = ws.getRange(2, 11, last - 1, 1).getValues();
  for (var i = 0; i < sites.length; i++) {
    var k = String(stats[i][0] || "").trim();
    if (k && k !== "New") {
      var d = domainOf_(sites[i][0]);
      if (d) out[d] = 1;
    }
  }
  return out;
}

function pendingRows_(limit) {
  var dir = tab_(DIRECTORY_SHEET, DIRECTORY_HEADERS);
  var out = tab_(OUTREACH_SHEET, OUTREACH_HEADERS);
  var handedKeys = {};
  columnValues_(out, 1).forEach(function (k) { handedKeys[k] = 1; });
  var handedDomains = {};
  columnValues_(out, 2).forEach(function (d) { handedDomains[d] = 1; });
  var contacted = contactedDomains_();
  var last = dir.getLastRow();
  if (last < 2) return [];
  var rows = dir.getRange(2, 1, last - 1, DIRECTORY_HEADERS.length).getValues();
  var iName = DIRECTORY_HEADERS.indexOf("name");
  var iWeb = DIRECTORY_HEADERS.indexOf("website");
  var iPc = DIRECTORY_HEADERS.indexOf("postcode");
  var iKey = DIRECTORY_HEADERS.indexOf("place_key");
  var pending = [];
  for (var i = 0; i < rows.length && pending.length < limit; i++) {
    var r = rows[i];
    var website = String(r[iWeb] || "").trim();
    if (!website) continue;
    var key = String(r[iKey] || "");
    var domain = domainOf_(website);
    if (!domain) continue;
    if (key && handedKeys[key]) continue;
    if (handedDomains[domain]) continue;
    if (contacted[domain]) continue;
    pending.push({ place_key: key, name: String(r[iName] || ""),
                   website: website, postcode: String(r[iPc] || ""),
                   domain: domain });
  }
  return pending;
}

function doGet(e) {
  try {
    var params = e.parameter || {};
    checkToken_(params.token);
    if (params.action === "pending") {
      var limit = parseInt(params.limit || "60", 10) || 60;
      return json_({ ok: true, rows: pendingRows_(limit) });
    }
    var dir = tab_(DIRECTORY_SHEET, DIRECTORY_HEADERS);
    var prog = tab_(PROGRESS_SHEET, PROGRESS_HEADERS);
    return json_({
      ok: true,
      done_keys: columnValues_(prog, 1),
      place_keys: columnValues_(dir, DIRECTORY_HEADERS.indexOf("place_key") + 1)
    });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var body = JSON.parse(e.postData.contents);
    checkToken_(body.token);
    var reply = { ok: true };

    if (body.outreach) {
      var out = tab_(OUTREACH_SHEET, OUTREACH_HEADERS);
      var existing = {};
      columnValues_(out, 1).forEach(function (k) { existing[k] = 1; });
      var stamp = new Date().toISOString();
      var rows = [];
      body.outreach.forEach(function (o) {
        if (o.place_key && existing[o.place_key]) return;
        existing[o.place_key] = 1;
        rows.push([o.place_key || "", o.domain || "", o.name || "",
                   o.outcome || "", stamp]);
      });
      if (rows.length) {
        out.getRange(out.getLastRow() + 1, 1, rows.length, OUTREACH_HEADERS.length).setValues(rows);
      }
      reply.outreach_added = rows.length;
      return json_(reply);
    }

    var dir = tab_(DIRECTORY_SHEET, DIRECTORY_HEADERS);
    var prog = tab_(PROGRESS_SHEET, PROGRESS_HEADERS);
    var d = body.directory || [];
    var p = body.progress || [];
    // Server-side dedupe: a retried POST must never duplicate rows.
    var pkCol = DIRECTORY_HEADERS.indexOf("place_key");
    var existingKeys = {};
    columnValues_(dir, pkCol + 1).forEach(function (k) { existingKeys[k] = 1; });
    d = d.filter(function (row) {
      var k = row[pkCol];
      if (k && existingKeys[k]) return false;
      existingKeys[k] = 1;
      return true;
    });
    var existingDone = {};
    columnValues_(prog, 1).forEach(function (k) { existingDone[k] = 1; });
    p = p.filter(function (row) { return !existingDone[row[0]]; });
    if (d.length) {
      dir.getRange(dir.getLastRow() + 1, 1, d.length, DIRECTORY_HEADERS.length).setValues(d);
    }
    if (p.length) {
      prog.getRange(prog.getLastRow() + 1, 1, p.length, PROGRESS_HEADERS.length).setValues(p);
    }
    reply.directory_added = d.length;
    reply.progress_added = p.length;
    return json_(reply);
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}
