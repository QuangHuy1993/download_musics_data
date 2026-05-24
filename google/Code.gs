const TOKEN = 'CHANGE_ME_SECRET_TOKEN';
const SUCCESS_SHEET = 'Data_music';
const ERROR_SHEET = 'Error';

function doPost(e) {
  try {
    const body = JSON.parse((e.postData && e.postData.contents) || '{}');

    if (body.token !== TOKEN) {
      return json({ ok: false, error: 'Unauthorized' });
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();

    if (body.type === 'success') {
      const sheet = ss.getSheetByName(SUCCESS_SHEET);
      writeSuccessRows(sheet, body.rows || []);
      return json({ ok: true });
    }

    if (body.type === 'error') {
      const sheet = ss.getSheetByName(ERROR_SHEET);
      writeErrorRows(sheet, body.rows || []);
      return json({ ok: true });
    }

    if (body.type === 'clear_test') {
      clearData(ss.getSheetByName(SUCCESS_SHEET), ss.getSheetByName(ERROR_SHEET));
      return json({ ok: true });
    }

    return json({ ok: false, error: 'Unknown type' });
  } catch (error) {
    return json({ ok: false, error: String(error) });
  }
}

function writeSuccessRows(sheet, rows) {
  const values = rows
    .sort((a, b) => Number(a.source_row || 0) - Number(b.source_row || 0))
    .map(row => [
      row.song_name || '',
      row.singer_name || '',
      row.lyrics || ''
    ]);

  if (values.length) {
    const startRow = sheet.getLastRow() + 1;
    sheet.getRange(startRow, 1, values.length, 3).setValues(values);
    sheet.getRange(startRow, 1, values.length, 3)
      .setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP)
      .setVerticalAlignment('middle');
    sheet.setRowHeightsForced(startRow, values.length, 24);
  }
}

function writeErrorRows(sheet, rows) {
  const values = rows
    .sort((a, b) => Number(a.source_row || 0) - Number(b.source_row || 0))
    .map(row => [
      row.input_song_name || row.song_name || '',
      row.song_url || '',
      row.error_message || ''
    ]);

  if (values.length) {
    const startRow = sheet.getLastRow() + 1;
    sheet.getRange(startRow, 1, values.length, 3).setValues(values);
    sheet.getRange(startRow, 1, values.length, 3)
      .setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP)
      .setVerticalAlignment('middle');
    sheet.setRowHeightsForced(startRow, values.length, 24);
  }
}

function clearData(successSheet, errorSheet) {
  const successLastRow = successSheet.getLastRow();
  if (successLastRow > 1) {
    successSheet.getRange(2, 1, successLastRow - 1, 3).clearContent();
    successSheet.setRowHeightsForced(2, successLastRow - 1, 24);
  }

  const errorLastRow = errorSheet.getLastRow();
  if (errorLastRow > 1) {
    errorSheet.getRange(2, 1, errorLastRow - 1, 3).clearContent();
    errorSheet.setRowHeightsForced(2, errorLastRow - 1, 24);
  }
}

function json(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
