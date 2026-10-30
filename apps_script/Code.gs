/**
 * mapscout directory endpoint.
 *
 * Setup: open the directory spreadsheet -> Extensions -> Apps Script ->
 * replace the editor contents with this file -> set TOKEN below to your
 * own secret word -> Deploy -> New deployment -> Web app
 * (Execute as: Me; Who has access: Anyone). Copy the web app URL into
 * the MAPSCOUT_API_URL GitHub secret, and TOKEN into MAPSCOUT_API_TOKEN.
 *
 * GET  ?token=...            -> {ok, done_keys, place_keys}
 * POST {token, directory:[rows], progress:[rows]} -> appends to the tabs.
 */
var TOKEN = "PASTE_YOUR_OWN_SECRET_TOKEN_HERE";
var DIRECTORY_SHEET = "Directory";
var PROGRESS_SHEET = "Progress";
var DIRECTORY_HEADERS = ["name","category","rating","reviews","address","postcode","town","phone","website","hours","maps_url","place_key","query","locality","niche","scraped_at"];
var PROGRESS_HEADERS = ["key","locality","niche","query","found","added","scraped_at"];

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

function doGet(e) {
  try {
    checkToken_((e.parameter || {}).token);
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
    var dir = tab_(DIRECTORY_SHEET, DIRECTORY_HEADERS);
    var prog = tab_(PROGRESS_SHEET, PROGRESS_HEADERS);
    var d = body.directory || [];
    var p = body.progress || [];
    if (d.length) {
      dir.getRange(dir.getLastRow() + 1, 1, d.length, DIRECTORY_HEADERS.length).setValues(d);
    }
    if (p.length) {
      prog.getRange(prog.getLastRow() + 1, 1, p.length, PROGRESS_HEADERS.length).setValues(p);
    }
    return json_({ ok: true, directory_added: d.length, progress_added: p.length });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}
