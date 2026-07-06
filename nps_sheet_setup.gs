/**
 * Elite Physiotherapy - NPS & Marketing sheet builder
 * =====================================================
 * Builds the 5-tab Google Sheet that the new NPS / marketing system writes to.
 * Companion docs: Elite_Marketing_Replacement_Plan.md (sec. 5), tally_nps_form.md
 *
 * HOW TO RUN (one-off, ~2 minutes):
 *   1. Create a new blank Google Sheet. Name it: Elite Physio - NPS & Marketing
 *   2. Extensions -> Apps Script.
 *   3. Delete anything in the editor, paste this whole file in.
 *   4. Click Save, then Run (choose the function "setupNpsMarketingSheet").
 *   5. First run asks you to authorise - approve it (it only touches this sheet).
 *   6. Switch back to the sheet: all 5 tabs are built. Done.
 *   7. Copy the sheet's ID from its URL (the long code between /d/ and /edit)
 *      and give it to Claude for config.py - it becomes the second SPREADSHEET_ID.
 *
 * Safe to re-run: it rebuilds the tabs from scratch each time. It will NOT
 * touch any tab whose name isn't one of the five below - but re-running
 * clears the five it owns, so don't re-run once real data is in them.
 */

var RAW = 'NPS - Raw Data';
var DASH = 'NPS - Dashboard';
var PHYSIO = 'NPS - Physio Breakdown';
var DET = 'NPS - Detractor Tracker';
var SENT = 'Marketing - Sent Log';

var CLINICS = ['Cookstown', 'Maghera', 'Omagh', 'Armagh'];
var TRIGGERS = [['Initial Assessment', 'ia'], ['Discharge', 'discharge'],
                ['Cancellation', 'cna'], ['No-show', 'dna']];
// These must match the physio_name value the Python system sends to Tally.
// NOTE: Molai's name is built from a char code (237 = i) on purpose. Writing the
// accented letter as a literal here once got double-encoded when this file was
// pasted into the Apps Script editor, so the breakdown stopped matching her raw
// rows. Keeping the source pure-ASCII makes that impossible. Do NOT replace it
// with a literal "Molai".
var PHYSIOS = ['Marty', 'Julie', 'Sinead', 'Erin', 'Daire', 'Aoife', 'Ciara',
               'Mola' + String.fromCharCode(237), 'Shannagh'];

var HEADER_BG = '#2A9EA7';
var SECTION_BG = '#33424E';
var BAND_BG = '#F1F5F7';


function setupNpsMarketingSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  buildRawData_(ss);
  buildDetractorTracker_(ss);
  buildSentLog_(ss);
  buildDashboard_(ss);
  buildPhysioBreakdown_(ss);
  // Move tabs into a sensible order.
  reorder_(ss, [DASH, PHYSIO, DET, RAW, SENT]);
  removeDefault_(ss);
  ss.toast('NPS & Marketing sheet built \u2014 5 tabs ready.', 'Done', 8);
}


/* ---------- helpers ---------- */

function freshSheet_(ss, name) {
  var s = ss.getSheetByName(name);
  if (s) {
    var all = s.getRange(1, 1, s.getMaxRows(), s.getMaxColumns());
    all.breakApart();           // un-merge so re-runs don't error
    all.clearDataValidations();
    s.clear();
    if (s.getFrozenRows() > 0) s.setFrozenRows(0);
  } else {
    s = ss.insertSheet(name);
  }
  return s;
}

function styleHeader_(sheet, row, numCols) {
  sheet.getRange(row, 1, 1, numCols)
    .setFontWeight('bold').setFontColor('#FFFFFF').setBackground(HEADER_BG)
    .setVerticalAlignment('middle').setWrap(true);
  sheet.setRowHeight(row, 34);
  sheet.setFrozenRows(row);
}

function sectionBar_(sheet, row, lastCol, text) {
  sheet.getRange(row, 1, 1, lastCol).merge()
    .setValue(text).setFontWeight('bold').setFontSize(11)
    .setFontColor('#FFFFFF').setBackground(SECTION_BG);
}

function reorder_(ss, names) {
  for (var i = 0; i < names.length; i++) {
    var s = ss.getSheetByName(names[i]);
    if (s) { ss.setActiveSheet(s); ss.moveActiveSheet(i + 1); }
  }
}

function removeDefault_(ss) {
  ['Sheet1', 'Sheet'].forEach(function (n) {
    var s = ss.getSheetByName(n);
    if (s && ss.getSheets().length > 1) ss.deleteSheet(s);
  });
}


/* ---------- Tab 1: NPS - Raw Data ---------- */

function buildRawData_(ss) {
  var s = freshSheet_(ss, RAW);
  var headers = ['Date Sent', 'Patient ID', 'Patient Name', 'Physio Name',
    'Clinic', 'Trigger Type', 'Score', 'Category', 'Open Text',
    'Callback Requested', 'Callback Number', 'Status', 'Date Responded',
    'Response ID'];
  s.getRange(1, 1, 1, headers.length).setValues([headers]);
  styleHeader_(s, 1, headers.length);

  // Column N (Response ID) is the Tally per-submission idempotency key - the
  // Python webhook skips writing a row whose Response ID is already present, so
  // a webhook retry / double-submit can't duplicate an NPS score. Hidden helper.
  var widths = [110, 90, 150, 110, 100, 110, 60, 95, 320, 120, 130, 150, 120, 150];
  widths.forEach(function (w, i) { s.setColumnWidth(i + 1, w); });
  s.hideColumns(14);

  // Status dropdown (column L)
  var statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['Survey sent', 'Response received', 'Callback completed'], true)
    .setAllowInvalid(true).build();
  s.getRange(2, 12, s.getMaxRows() - 1, 1).setDataValidation(statusRule);

  s.getRange('A2:A').setNumberFormat('dd mmm yyyy');
  s.getRange('M2:M').setNumberFormat('dd mmm yyyy');
  s.getRange(1, 1, 1, 1).setNote(
    'One row per survey. The Python marketing system appends rows here ' +
    'and the Tally webhook fills in Score, Category, Open Text etc. on response.');
}


/* ---------- Tab 2: NPS - Detractor Tracker ---------- */

function buildDetractorTracker_(ss) {
  var s = freshSheet_(ss, DET);
  var headers = ['Date Responded', 'Patient Name', 'Patient ID', 'Physio',
    'Clinic', 'Trigger', 'Score', 'What they told us', 'Callback Requested',
    'Callback Number', 'Resolution Status', 'Resolution Notes', 'Resolved By',
    'Date Resolved'];
  s.getRange(1, 1, 1, headers.length).setValues([headers]);
  styleHeader_(s, 1, headers.length);

  var widths = [120, 150, 90, 110, 100, 110, 60, 320, 120, 130, 140, 300, 120, 120];
  widths.forEach(function (w, i) { s.setColumnWidth(i + 1, w); });

  // Resolution Status dropdown (column K) - the columns Sinead fills in by hand.
  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['Pending', 'In progress', 'Resolved', 'Closed \u2014 no response'], true)
    .setAllowInvalid(true).build();
  s.getRange(2, 11, s.getMaxRows() - 1, 1).setDataValidation(rule);

  s.getRange('A2:A').setNumberFormat('dd mmm yyyy');
  s.getRange('N2:N').setNumberFormat('dd mmm yyyy');
  // Tint the manual columns so it's obvious which ones the team completes.
  s.getRange(2, 11, s.getMaxRows() - 1, 4).setBackground('#FFF7E6');
  s.getRange(1, 1, 1, 1).setNote(
    'The Python webhook appends a row here for every detractor. ' +
    'Columns K\u2013N (shaded) are completed by Sinead as the callback is worked.');
}


/* ---------- Tab 3: Marketing - Sent Log ---------- */

function buildSentLog_(ss) {
  var s = freshSheet_(ss, SENT);
  var headers = ['Timestamp', 'Patient ID', 'Patient Name', 'Flow Name',
    'Channel', 'Episode Anchor Date', 'Template Used', 'Status'];
  s.getRange(1, 1, 1, headers.length).setValues([headers]);
  styleHeader_(s, 1, headers.length);

  var widths = [160, 90, 150, 180, 90, 150, 200, 120];
  widths.forEach(function (w, i) { s.setColumnWidth(i + 1, w); });

  s.getRange('A2:A').setNumberFormat('dd mmm yyyy  hh:mm');
  s.getRange('F2:F').setNumberFormat('dd mmm yyyy');
  s.getRange(1, 1, 1, 1).setNote(
    'Dedup ledger. Every email/SMS the marketing system sends writes one row. ' +
    'The poller checks this tab before sending so no patient is messaged twice.');
}


/* ---------- Tab 4: NPS - Dashboard ---------- */

function buildDashboard_(ss) {
  var s = freshSheet_(ss, DASH);
  var q = "'" + RAW + "'!";        // raw sheet ref for formulas
  var dt = "'" + DET + "'!";       // detractor tracker ref
  var sl = "'" + SENT + "'!";      // sent log ref (surveys-sent count)

  s.getRange('A1').setValue('Elite Physiotherapy \u2014 NPS Dashboard');
  s.getRange('A1:F1').merge().setFontSize(16).setFontWeight('bold').setFontColor('#33424E');
  s.getRange('A2:F2').merge().setValue(
    'NPS = % Promoters \u2212 % Detractors (range \u2212100 to +100). '
    + 'Updates automatically as responses arrive. Promoter 9\u201310, Passive 7\u20138, Detractor 0\u20136.')
    .setFontColor('#6B7984').setFontStyle('italic');
  s.setColumnWidth(1, 210);
  for (var c = 2; c <= 6; c++) s.setColumnWidth(c, 110);

  // --- HEADLINE ---
  sectionBar_(s, 4, 6, 'HEADLINE');
  var head = [
    ['Overall NPS (all-time)',
      '=IFERROR(ROUND((COUNTIF(' + q + 'G:G,">=9")-COUNTIF(' + q + 'G:G,"<=6"))/COUNT(' + q + 'G:G)*100),"No data")'],
    ['Overall NPS (last 90 days)',
      '=IFERROR(ROUND((COUNTIFS(' + q + 'G:G,">=9",' + q + 'M:M,">="&(TODAY()-90))-COUNTIFS(' + q + 'G:G,"<=6",' + q + 'M:M,">="&(TODAY()-90)))/COUNTIFS(' + q + 'G:G,">=0",' + q + 'M:M,">="&(TODAY()-90))*100),"No data")'],
    ['Surveys sent', '=COUNTIF(' + sl + 'D:D,"*survey_sms")'],
    ['Responses received', '=COUNT(' + q + 'G2:G)'],
    ['Response rate', '=IFERROR(ROUND(COUNT(' + q + 'G2:G)/COUNTIF(' + sl + 'D:D,"*survey_sms")*100)&"%","\u2014")']
  ];
  for (var i = 0; i < head.length; i++) {
    s.getRange(5 + i, 1).setValue(head[i][0]).setFontWeight('bold');
    s.getRange(5 + i, 2).setFormula(head[i][1]);
  }

  // --- BY CLINIC ---
  var rowClinicHdr = 11;
  sectionBar_(s, rowClinicHdr, 6, 'BY CLINIC');
  tableHeader_(s, rowClinicHdr + 1, ['Clinic', 'Responses', 'Promoters', 'Passives', 'Detractors', 'NPS']);
  for (var ci = 0; ci < CLINICS.length; ci++) {
    var r = rowClinicHdr + 2 + ci;
    s.getRange(r, 1).setValue(CLINICS[ci]);
    countRow_(s, r, q, 'E:E', '$A' + r);
  }

  // --- BY TRIGGER ---
  var rowTrigHdr = rowClinicHdr + 2 + CLINICS.length + 1;   // 18
  sectionBar_(s, rowTrigHdr, 6, 'BY TRIGGER');
  tableHeader_(s, rowTrigHdr + 1, ['Trigger', 'Responses', 'Promoters', 'Passives', 'Detractors', 'NPS']);
  for (var ti = 0; ti < TRIGGERS.length; ti++) {
    var rt = rowTrigHdr + 2 + ti;
    s.getRange(rt, 1).setValue(TRIGGERS[ti][0]);
    countRowLiteral_(s, rt, q, 'F:F', '"' + TRIGGERS[ti][1] + '"');
  }

  // --- BY PHYSIO ---
  var rowPhysHdr = rowTrigHdr + 2 + TRIGGERS.length + 1;    // 25
  sectionBar_(s, rowPhysHdr, 6, 'BY PHYSIO');
  tableHeader_(s, rowPhysHdr + 1, ['Physio', 'Responses', 'Promoters', 'Passives', 'Detractors', 'NPS']);
  for (var pi = 0; pi < PHYSIOS.length; pi++) {
    var rp = rowPhysHdr + 2 + pi;
    s.getRange(rp, 1).setValue(PHYSIOS[pi]);
    countRow_(s, rp, q, 'D:D', '$A' + rp);
  }

  // --- DETRACTOR FOLLOW-UP ---
  var rowDet = rowPhysHdr + 2 + PHYSIOS.length + 1;         // 36
  sectionBar_(s, rowDet, 6, 'DETRACTOR FOLLOW-UP');
  var det = [
    ['Detractors (all-time)', '=COUNTIF(' + q + 'G:G,"<=6")'],
    ['Callbacks requested', '=COUNTIFS(' + q + 'G:G,"<=6",' + q + 'J:J,"Yes")'],
    ['Callbacks resolved', '=COUNTIF(' + dt + 'K:K,"Resolved")'],
    ['Callback resolution rate',
      '=IFERROR(ROUND(COUNTIF(' + dt + 'K:K,"Resolved")/COUNTIFS(' + q + 'G:G,"<=6",' + q + 'J:J,"Yes")*100)&"%","\u2014")']
  ];
  for (var di = 0; di < det.length; di++) {
    s.getRange(rowDet + 1 + di, 1).setValue(det[di][0]).setFontWeight('bold');
    s.getRange(rowDet + 1 + di, 2).setFormula(det[di][1]);
  }

  // --- MONTHLY TREND (most recent month at top) ---
  var rowMon = rowDet + det.length + 2;                     // 42
  sectionBar_(s, rowMon, 5, 'MONTHLY TREND');
  tableHeader_(s, rowMon + 1, ['Month', 'Responses', 'Promoters', 'Detractors', 'NPS']);
  for (var m = 0; m < 12; m++) {
    var rm = rowMon + 2 + m;
    if (m === 0) {
      s.getRange(rm, 1).setFormula('=DATE(YEAR(TODAY()),MONTH(TODAY()),1)');
    } else {
      s.getRange(rm, 1).setFormula('=EDATE(A' + (rm - 1) + ',-1)');
    }
    s.getRange(rm, 1).setNumberFormat('mmm yyyy');
    var win = q + 'M:M,">="&$A' + rm + ',' + q + 'M:M,"<"&EDATE($A' + rm + ',1)';
    s.getRange(rm, 2).setFormula('=COUNTIFS(' + q + 'G:G,">=0",' + win + ')');
    s.getRange(rm, 3).setFormula('=COUNTIFS(' + q + 'G:G,">=9",' + win + ')');
    s.getRange(rm, 4).setFormula('=COUNTIFS(' + q + 'G:G,"<=6",' + win + ')');
    s.getRange(rm, 5).setFormula('=IFERROR(ROUND((C' + rm + '-D' + rm + ')/B' + rm + '*100),"\u2014")');
  }

  s.getRange(rowMon + 14, 1).setValue(
    'Tip: select the Monthly Trend table and Insert \u2192 Chart for a visual trend line.')
    .setFontColor('#6B7984').setFontStyle('italic');

  s.setHiddenGridlines(true);
}

function tableHeader_(s, row, labels) {
  s.getRange(row, 1, 1, labels.length).setValues([labels])
    .setFontWeight('bold').setBackground(BAND_BG).setBorder(true, true, true, true, true, true,
      '#D7DEE3', SpreadsheetApp.BorderStyle.SOLID);
}

// Count row where criterion column equals the label cell (e.g. clinic / physio name).
function countRow_(s, r, q, col, labelCell) {
  s.getRange(r, 2).setFormula('=COUNTIFS(' + q + col + ',' + labelCell + ',' + q + 'G:G,">=0")');
  s.getRange(r, 3).setFormula('=COUNTIFS(' + q + col + ',' + labelCell + ',' + q + 'G:G,">=9")');
  s.getRange(r, 4).setFormula('=COUNTIFS(' + q + col + ',' + labelCell + ',' + q + 'G:G,">=7",' + q + 'G:G,"<=8")');
  s.getRange(r, 5).setFormula('=COUNTIFS(' + q + col + ',' + labelCell + ',' + q + 'G:G,"<=6")');
  s.getRange(r, 6).setFormula('=IFERROR(ROUND((C' + r + '-E' + r + ')/B' + r + '*100),"\u2014")');
}

// Same, but criterion is a literal string (e.g. trigger code "ia").
function countRowLiteral_(s, r, q, col, lit) {
  s.getRange(r, 2).setFormula('=COUNTIFS(' + q + col + ',' + lit + ',' + q + 'G:G,">=0")');
  s.getRange(r, 3).setFormula('=COUNTIFS(' + q + col + ',' + lit + ',' + q + 'G:G,">=9")');
  s.getRange(r, 4).setFormula('=COUNTIFS(' + q + col + ',' + lit + ',' + q + 'G:G,">=7",' + q + 'G:G,"<=8")');
  s.getRange(r, 5).setFormula('=COUNTIFS(' + q + col + ',' + lit + ',' + q + 'G:G,"<=6")');
  s.getRange(r, 6).setFormula('=IFERROR(ROUND((C' + r + '-E' + r + ')/B' + r + '*100),"\u2014")');
}


/* ---------- Tab 5: NPS - Physio Breakdown (private to Marty) ---------- */

function buildPhysioBreakdown_(ss) {
  var s = freshSheet_(ss, PHYSIO);
  var q = "'" + RAW + "'!";

  s.getRange('A1').setValue('NPS \u2014 Physio Breakdown  (private \u2014 for coaching)');
  s.getRange('A1:H1').merge().setFontSize(14).setFontWeight('bold').setFontColor('#33424E');
  s.getRange('A2:H2').merge().setValue(
    'Per-physio NPS and the comments behind it. Keep this tab restricted to Marty.')
    .setFontColor('#6B7984').setFontStyle('italic');
  s.setColumnWidth(1, 120);
  for (var c = 2; c <= 7; c++) s.setColumnWidth(c, 105);
  s.setColumnWidth(8, 60);

  // Per-physio table
  tableHeader_(s, 4, ['Physio', 'Responses', 'Promoters', 'Passives',
    'Detractors', 'NPS all-time', 'NPS 90-day']);
  for (var i = 0; i < PHYSIOS.length; i++) {
    var r = 5 + i;
    s.getRange(r, 1).setValue(PHYSIOS[i]);
    s.getRange(r, 2).setFormula('=COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,">=0")');
    s.getRange(r, 3).setFormula('=COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,">=9")');
    s.getRange(r, 4).setFormula('=COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,">=7",' + q + 'G:G,"<=8")');
    s.getRange(r, 5).setFormula('=COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,"<=6")');
    s.getRange(r, 6).setFormula('=IFERROR(ROUND((C' + r + '-E' + r + ')/B' + r + '*100),"\u2014")');
    s.getRange(r, 7).setFormula(
      '=IFERROR(ROUND((COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,">=9",' + q + 'M:M,">="&(TODAY()-90))'
      + '-COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,"<=6",' + q + 'M:M,">="&(TODAY()-90)))'
      + '/COUNTIFS(' + q + 'D:D,$A' + r + ',' + q + 'G:G,">=0",' + q + 'M:M,">="&(TODAY()-90))*100),"\u2014")');
  }

  // Comments viewer
  var cRow = 5 + PHYSIOS.length + 2;            // ~15
  sectionBar_(s, cRow, 8, 'COMMENTS \u2014 choose a physio');
  s.getRange(cRow + 1, 1).setValue('View comments for:').setFontWeight('bold');
  var picker = s.getRange(cRow + 1, 2);
  picker.setValue(PHYSIOS[0]).setBackground('#FFF7E6');
  picker.setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInList(PHYSIOS, true).build());

  tableHeader_(s, cRow + 3, ['Date', 'Patient', 'Score', 'Comment']);
  s.getRange(cRow + 4, 1).setFormula(
    '=IFERROR(FILTER({' + q + 'A2:A,' + q + 'C2:C,' + q + 'G2:G,' + q + 'I2:I},'
    + q + 'D2:D=$B$' + (cRow + 1) + ',' + q + 'I2:I<>""),"No comments yet for this physio")');
  s.getRange(cRow + 4, 1, 200, 1).setNumberFormat('dd mmm yyyy');

  s.setHiddenGridlines(true);
}
