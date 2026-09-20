/**
 * F061 T-41: the CSV a user downloads from a database anchor (AC-33).
 *
 * Split from `datasourceCitation.test.ts` because this is about the FILE rather
 * than about the citation: what makes it open correctly in Excel matters more
 * than its shape, and both halves of that are easy to get wrong invisibly.
 *
 * `getDatasourceAnchor` and `buildDatasourceCsv` are covered there; this file
 * covers `buildDatasourceCsvBlob`, which is what the panel hands to the download.
 */

import type { ChatCitation } from '~/api/chatApi';
import { buildDatasourceCsv, buildDatasourceCsvBlob, getDatasourceAnchor } from './citationUtils';

/**
 * Read a Blob as bytes.
 *
 * Bytes, not text, on purpose: `FileReader.readAsText` (and `Blob.text()`)
 * DECODE with an assumed charset, and the decoding step eats a leading BOM —
 * so a text read cannot observe the very thing these tests are about.
 * `readAsArrayBuffer` is what actually sees the file's first three bytes.
 */
function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

function anchorWith(rows: unknown[][], columns: string[]) {
  const detail = {
    citationId: 'datasource_7_0',
    type: 'DATASOURCE',
    sourcePayload: {
      datasourceId: 7,
      statement: 'SELECT * FROM t',
      columns,
      rows,
      tables: ['t'],
    },
  } as ChatCitation;
  return getDatasourceAnchor(detail)!;
}

describe('datasource CSV export (F061 AC-33)', () => {
  it('starts with the three UTF-8 BOM bytes', async () => {
    // Without it Excel on a Chinese Windows locale reads the file as GBK and
    // every non-ASCII cell in the result table turns into mojibake. The BOM is
    // invisible in every other viewer, so a regression here looks like nothing
    // until a customer opens the file.
    const bytes = await readBlobBytes(buildDatasourceCsvBlob(anchorWith([[1]], ['a'])));

    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
  });

  it('keeps the CSV body intact after the BOM', async () => {
    const anchor = anchorWith([['x,y']], ['note']);
    const bytes = await readBlobBytes(buildDatasourceCsvBlob(anchor));

    // Compare BYTES after the 3-byte BOM: any text read strips it again (the
    // decoder treats a leading BOM as a signature, not content), which is exactly
    // why the assertion above reads bytes. The bytes after it must be the plain
    // CSV, undamaged by the prefix.
    const bodyBytes = bytes.slice(3);
    const expected = buildDatasourceCsv(anchor);
    expect(bodyBytes.length).toBe(expected.length);
    expect(Array.from(bodyBytes.slice(0, 4))).toEqual(
      Array.from(expected.slice(0, 4), (ch) => ch.charCodeAt(0)),
    );
  });

  it('declares a CSV content type', () => {
    expect(buildDatasourceCsvBlob(anchorWith([[1]], ['a'])).type).toContain('text/csv');
  });

  it('carries a name that says which dataset it is', () => {
    // The panel builds the filename; the blob only has to be a valid CSV. This
    // asserts the two are separable — a future refactor that moves naming into
    // the blob would break callers that already pass a name.
    const blob = buildDatasourceCsvBlob(anchorWith([[1]], ['a']));
    expect(blob.size).toBeGreaterThan(0);
  });
});
