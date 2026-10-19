import * as XLSX from 'xlsx';

export type SheetData = string[][];

export function parseCSV(csvStr: string): SheetData {
  try {
    if (!csvStr || typeof csvStr !== 'string') return [];
    const lines = csvStr.split(/\r?\n/).filter((line) => line.trim() !== '');
    const rows: SheetData = [];
    const delimiters = [',', '\t', ';', '|'];

    let detectedDelimiter = ',';
    let maxColumns = 0;
    for (const delimiter of delimiters) {
      const testRow = lines[0]?.split(delimiter) || [];
      if (testRow.length > maxColumns && testRow.some((col) => col.trim() !== '')) {
        maxColumns = testRow.length;
        detectedDelimiter = delimiter;
      }
    }

    lines.forEach((line) => {
      const columns = line.split(detectedDelimiter).map((col) => col.replace(/^["']|["']$/g, '').trim());
      if (columns.some((col) => col !== '')) rows.push(columns);
    });
    return rows;
  } catch (err) {
    console.error('CSV parsing error:', err);
    return [];
  }
}

export function cleanData(data: unknown[][]): SheetData {
  if (!Array.isArray(data) || data.length === 0) return [];

  const nonEmptyRows = data.filter((row) =>
    row.some((cell) => cell !== undefined && cell !== null && String(cell).trim() !== ''),
  );
  if (nonEmptyRows.length === 0) return [];

  const columnCount = Math.max(...nonEmptyRows.map((row) => row.length));
  const hasDataColumns: number[] = [];
  for (let col = 0; col < columnCount; col++) {
    const hasData = nonEmptyRows.some(
      (row) => row[col] !== undefined && row[col] !== null && String(row[col]).trim() !== '',
    );
    if (hasData) hasDataColumns.push(col);
  }

  return nonEmptyRows.map((row) => hasDataColumns.map((colIndex) => (row[colIndex] == null ? '' : String(row[colIndex]).trim())));
}

export function readWorksheet(sheet: XLSX.WorkSheet): SheetData {
  // Use cached formula results with number formats; this viewer does not calculate formulas.
  return cleanData(XLSX.utils.sheet_to_json<unknown[]>(sheet, {
    header: 1,
    defval: '',
    raw: false,
  }));
}
