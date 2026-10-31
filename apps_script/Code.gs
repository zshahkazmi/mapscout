/**
 * mapscout directory endpoint (v3 — parallel outreach with claims).
 *
 * Setup: open the directory spreadsheet -> Extensions -> Apps Script ->
 * replace the editor contents with this file -> set TOKEN below to your
 * own secret word -> Deploy -> Manage deployments -> New version ->
 * Deploy (same URL as before; repo secrets unchanged).
 *
 * GET  ?token=...                       -> {ok, done_keys, place_keys}
 * GET  ?token=...&action=pending&limit=N
 *      -> claims and returns up to N businesses for outreach:
 *         directory rows first (website present), then businesses from
 *         the main lead sheet that were never contacted. A claimed
 *         business is locked for 12 hours (so parallel runners never
 *         take the same one) and becomes available again only if no
 *         final outcome was recorded for it.
 * POST {token, directory:[rows], progress:[rows]}  -> appends (deduped).
 * POST {token, outreach:[{place_key, domain, name, outcome}]}
 *      -> records FINAL outcomes in the Outreach ledger (a business
 *         with a final outcome is never handed out again). For
 *         lead-sheet businesses (place_key "lead-<row>") the outcome
 *         is also written to that row's status column on the lead
 *         sheet, if it is still blank/New there.
 */
var TOKEN = "PASTE_YOUR_OWN_SECRET_TOKEN_HERE";
var DIRECTORY_SHEET = "Directory";
var PROGRESS_SHEET = "Progress";
var OUTREACH_SHEET = "Outreach";
var LEAD_SHEET_ID = "1QvNmxurmL3FbcAGR8TofSOkaZbM_7z9_63GscQB5A48";
var CLAIM_TTL_MS = 12 * 60 * 60 * 1000;
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

/** One pass over the main lead sheet: which domains were already
 *  contacted (any terminal status in column K), and the businesses
 *  never contacted (first row per domain, postcode from the address). */
function leadSheetData_() {
  var book = SpreadsheetApp.openById(LEAD_SHEET_ID);
  var ws = book.getSheetByName("Sheet1");
  var last = ws.getLastRow();
  var contacted = {};
  var candidates = [];
  if (last < 2) return { contacted: contacted, candidates: candidates };
  var names = ws.getRange(2, 1, last - 1, 1).getValues();
  var sites = ws.getRange(2, 5, last - 1, 1).getValues();
  var addrs = ws.getRange(2, 6, last - 1, 1).getValues();
  var stats = ws.getRange(2, 11, last - 1, 1).getValues();
  var byDomain = {};
  var order = [];
  for (var i = 0; i < sites.length; i++) {
    var d = domainOf_(sites[i][0]);
    if (!d) continue;
    var k = String(stats[i][0] || "").trim();
    if (k && k !== "New") contacted[d] = 1;
    if (!byDomain[d]) {
      byDomain[d] = { row: i + 2, name: String(names[i][0] || ""),
                      website: String(sites[i][0] || ""),
                      address: String(addrs[i][0] || "") };
      order.push(d);
    }
  }
  order.forEach(function (d) {
    if (contacted[d]) return;
    var info = byDomain[d];
    var m = /([A-Za-z]{1,2}\d[A-Za-z\d]?\s*\d[A-Za-z]{2})\s*$/.exec(info.address || "");
    candidates.push({ place_key: "lead-" + info.row, name: info.name,
                      website: info.website,
                      postcode: m ? m[1].toUpperCase() : "", domain: d });
  });
  return { contacted: contacted, candidates: candidates };
}

/** The Outreach ledger reduced to what decisions need: the latest row
 *  per place_key, plus the sets of keys/domains with a FINAL outcome
 *  (anything other than "claimed"). */
function outreachState_() {
  var out = tab_(OUTREACH_SHEET, OUTREACH_HEADERS);
  var last = out.getLastRow();
  var latestByKey = {}, finalDomains = {}, finalKeys = {};
  if (last >= 2) {
    var rows = out.getRange(2, 1, last - 1, OUTREACH_HEADERS.length).getValues();
    for (var i = 0; i < rows.length; i++) {
      var key = String(rows[i][0] || "");
      var domain = String(rows[i][1] || "");
      var outcome = String(rows[i][3] || "");
      var when = rows[i][4] ? new Date(rows[i][4]).getTime() : 0;
      if (key) latestByKey[key] = { outcome: outcome, time: when };
      if (outcome && outcome !== "claimed") {
        if (domain) finalDomains[domain] = 1;
        if (key) finalKeys[key] = 1;
      }
    }
  }
  return { latestByKey: latestByKey, finalDomains: finalDomains,
           finalKeys: finalKeys };
}

/** Sheet status text for a formscout outcome (lead-sheet write-back). */
function statusForOutcome_(outcome, dateStr) {
  switch (outcome) {
    case "sent": return "Sent " + dateStr;
    case "uncertain": return "Uncertain " + dateStr +
      " - submitted via formscout, no confirmation shown; do not retry";
    case "captcha_review": return "CAPTCHA failed - user to review";
    case "no_form": return "No contact form on site";
    case "site_down": return "Site unreachable - skipped " + dateStr;
    case "form_error": return "Form error " + dateStr;
    default: return null;
  }
}

/** Claim-and-return the next batch. The caller (doGet) holds the
 *  script lock, so parallel pulls can never select the same rows. */
function pendingRows_(limit) {
  var ostate = outreachState_();
  var lead = leadSheetData_();
  var now = Date.now();
  var seenDomains = {};
  var pending = [];
  function eligible(key, domain) {
    if (!domain || seenDomains[domain]) return false;
    if (ostate.finalDomains[domain] || lead.contacted[domain]) return false;
    var st = key ? ostate.latestByKey[key] : null;
    if (st) {
      if (st.outcome !== "claimed") return false;
      if (now - st.time < CLAIM_TTL_MS) return false;
    }
    return true;
  }
  function take(item) { seenDomains[item.domain] = 1; pending.push(item); }

  var dir = tab_(DIRECTORY_SHEET, DIRECTORY_HEADERS);
  var dlast = dir.getLastRow();
  if (dlast >= 2) {
    var rows = dir.getRange(2, 1, dlast - 1, DIRECTORY_HEADERS.length).getValues();
    var iName = DIRECTORY_HEADERS.indexOf("name");
    var iWeb = DIRECTORY_HEADERS.indexOf("website");
    var iPc = DIRECTORY_HEADERS.indexOf("postcode");
    var iKey = DIRECTORY_HEADERS.indexOf("place_key");
    for (var i = 0; i < rows.length && pending.length < limit; i++) {
      var website = String(rows[i][iWeb] || "").trim();
      if (!website) continue;
      var domain = domainOf_(website);
      var key = String(rows[i][iKey] || "");
      if (!eligible(key, domain)) continue;
      take({ place_key: key, name: String(rows[i][iName] || ""),
             website: website, postcode: String(rows[i][iPc] || ""),
             domain: domain });
    }
  }
  for (var j = 0; j < lead.candidates.length && pending.length < limit; j++) {
    var c = lead.candidates[j];
    if (!eligible(c.place_key, c.domain)) continue;
    take(c);
  }
  if (pending.length) {
    var out = tab_(OUTREACH_SHEET, OUTREACH_HEADERS);
    var stamp = new Date().toISOString();
    var claimRows = pending.map(function (p) {
      return [p.place_key, p.domain, p.name, "claimed", stamp];
    });
    out.getRange(out.getLastRow() + 1, 1, claimRows.length,
                 OUTREACH_HEADERS.length).setValues(claimRows);
  }
  return pending;
}

function doGet(e) {
  try {
    var params = e.parameter || {};
    checkToken_(params.token);
    if (params.action === "pending") {
      var limit = parseInt(params.limit || "120", 10) || 120;
      var lock = LockService.getScriptLock();
      lock.waitLock(60000);
      try {
        return json_({ ok: true, rows: pendingRows_(limit) });
      } finally {
        lock.releaseLock();
      }
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
      var ostate = outreachState_();
      var out = tab_(OUTREACH_SHEET, OUTREACH_HEADERS);
      var stamp = new Date().toISOString();
      var dateStr = stamp.slice(0, 10);
      var rows = [];
      var leadWrites = [];
      body.outreach.forEach(function (o) {
        var key = String(o.place_key || "");
        if (!key || ostate.finalKeys[key]) return;
        ostate.finalKeys[key] = 1;
        rows.push([key, o.domain || "", o.name || "", o.outcome || "", stamp]);
        if (key.indexOf("lead-") === 0) {
          var st = statusForOutcome_(o.outcome || "", dateStr);
          if (st) leadWrites.push({ row: parseInt(key.slice(5), 10), status: st });
        }
      });
      if (rows.length) {
        out.getRange(out.getLastRow() + 1, 1, rows.length,
                     OUTREACH_HEADERS.length).setValues(rows);
      }
      if (leadWrites.length) {
        var book = SpreadsheetApp.openById(LEAD_SHEET_ID);
        var ws = book.getSheetByName("Sheet1");
        leadWrites.forEach(function (w) {
          var cell = ws.getRange(w.row, 11);
          var cur = String(cell.getValue() || "").trim();
          if (!cur || cur === "New") cell.setValue(w.status);
        });
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
