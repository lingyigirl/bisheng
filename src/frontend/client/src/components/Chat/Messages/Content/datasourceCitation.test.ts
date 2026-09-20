/**
 * F061 T-41: a database answer's anchor must be recognisable and renderable.
 *
 * The bug this pins down is a misclassification, not a missing feature. The
 * client's citation layer was written for two kinds — documents and web pages —
 * and `normalizeCitationType` returns 'rag' for anything that is not web. So a
 * DATASOURCE anchor (which the resolve endpoint already validates and returns)
 * was classified as a document: the panel labelled it "文档", the expander
 * offered a document preview, and the SQL and result table had nowhere to go.
 *
 * These assertions are on the pure functions rather than on JSX: the label and
 * the anchor/CSV shapes are where the wrong-type bug lives, and they need no
 * drawer mounted to test.
 *
 * Fixture text is written with `\u` escapes rather than literal CJK. The i18n
 * lint rule bans hardcoded Chinese in source, and for a DATABASE NAME the point
 * is to prove the value round-trips whatever it is — using an escape makes that
 * explicit instead of incidental.
 */

import type { ChatCitation } from '~/api/chatApi';
import {
  DATASOURCE_CITATION_TYPE,
  buildDatasourceCsv,
  getCitationSourceLabelI18n,
  getCitationSourceKind,
  getDatasourceAnchor,
  normalizeCitationType,
} from './citationUtils';

/**
 * A datasource name in the client's real (Chinese) locale, built from code
 * points so the value is CJK without the source carrying a CJK literal — the
 * i18n lint rule bans those, and the point of the fixture is that a datasource
 * NAME of any script round-trips.
 */
const DATASOURCE_NAME = String.fromCharCode(
  0x5ba2, 0x6237, 0x8ba2, 0x5355, 0x5e93,
);

function citation(partial: Partial<ChatCitation> & { type: string }): ChatCitation {
  return { citationId: 'x', ...partial } as ChatCitation;
}

const DATASOURCE_DETAIL = citation({
  citationId: 'datasource_7_0',
  type: DATASOURCE_CITATION_TYPE,
  sourcePayload: {
    datasourceId: 7,
    datasourceName: DATASOURCE_NAME,
    tables: ['orders', 'customers'],
    statement: 'SELECT COUNT(*) FROM orders',
    columns: ['count'],
    rows: [[1234]],
    truncated: false,
  },
});

const RAG_DETAIL = citation({
  citationId: 'knowledgesearch_abc',
  type: 'RAG',
  sourcePayload: { documentName: 'handbook.pdf' },
});

const WEB_DETAIL = citation({
  citationId: 'websearch_xyz',
  type: 'WEB',
  sourcePayload: { url: 'https://example.com', title: 'example' },
});

describe('datasource citation classification (F061 T-41)', () => {
  it('does not call a datasource anchor a document', () => {
    // The regression this file exists for: 'rag' sent the anchor down the
    // document path, where its payload has no documentName and its rows and
    // statement are silently dropped.
    expect(normalizeCitationType('DATASOURCE')).not.toBe('rag');
    expect(normalizeCitationType('datasource')).not.toBe('rag');
  });

  it('reports the datasource kind for every spelling the backend can send', () => {
    expect(getCitationSourceKind('DATASOURCE')).toBe('datasource');
    expect(getCitationSourceKind('datasource')).toBe('datasource');
    expect(getCitationSourceKind('Datasource')).toBe('datasource');
  });

  it('keeps the existing kinds unchanged', () => {
    expect(getCitationSourceKind('RAG')).toBe('rag');
    expect(getCitationSourceKind('knowledgesearch_abc')).toBe('rag');
    expect(getCitationSourceKind('WEB')).toBe('web');
    expect(getCitationSourceKind('websearch')).toBe('web');
  });

  it('labels each kind through i18n, so the datasource is never called a document', () => {
    const localize = (key: string) => key;

    expect(getCitationSourceLabelI18n('datasource', localize)).toBe(
      'com_citation_source_datasource',
    );
    expect(getCitationSourceLabelI18n('web', localize)).toBe('com_citation_source_web');
    expect(getCitationSourceLabelI18n('rag', localize)).toBe('com_citation_source_document');
  });

  it('exposes the backend type string as one constant', () => {
    // Must match `CitationType.DATASOURCE` on the backend, which arrives
    // uppercased by the enum's value. Spelled twice it would drift.
    expect(DATASOURCE_CITATION_TYPE).toBe('DATASOURCE');
  });
});

describe('datasource anchor extraction (F061 AC-31/32/33)', () => {
  it('reads the anchor the backend persisted', () => {
    const anchor = getDatasourceAnchor(DATASOURCE_DETAIL);

    expect(anchor).not.toBeNull();
    expect(anchor!.datasourceName).toBe(DATASOURCE_NAME);
    expect(anchor!.tables).toEqual(['orders', 'customers']);
    expect(anchor!.statement).toBe('SELECT COUNT(*) FROM orders');
    expect(anchor!.columns).toEqual(['count']);
    expect(anchor!.rows).toEqual([[1234]]);
    expect(anchor!.truncated).toBe(false);
    expect(anchor!.hasRows).toBe(true);
  });

  it('returns null for a document or a web page', () => {
    expect(getDatasourceAnchor(RAG_DETAIL)).toBeNull();
    expect(getDatasourceAnchor(WEB_DETAIL)).toBeNull();
    expect(getDatasourceAnchor(null)).toBeNull();
    expect(getDatasourceAnchor(undefined)).toBeNull();
  });

  it('survives a payload with no rows at all', () => {
    // A query can legitimately match nothing; the panel still shows the
    // statement, and the download button must not appear for an empty table.
    const anchor = getDatasourceAnchor(
      citation({
        citationId: 'datasource_7_1',
        type: DATASOURCE_CITATION_TYPE,
        sourcePayload: {
          datasourceId: 7,
          tables: ['orders'],
          statement: 'SELECT id FROM orders WHERE 1 = 0',
          columns: ['id'],
          rows: [],
        },
      }),
    );

    expect(anchor!.hasRows).toBe(false);
    expect(anchor!.statement).toContain('1 = 0');
  });

  it('tolerates a malformed payload instead of throwing', () => {
    // The anchor is persisted data read back on history reload; a row written by
    // an older process must degrade, not break the whole message.
    const anchor = getDatasourceAnchor(
      citation({
        citationId: 'datasource_7_2',
        type: DATASOURCE_CITATION_TYPE,
        sourcePayload: { rows: 'not-an-array', columns: null, tables: undefined },
      }),
    );

    expect(anchor!.rows).toEqual([]);
    expect(anchor!.columns).toEqual([]);
    expect(anchor!.tables).toEqual([]);
    expect(anchor!.hasRows).toBe(false);
  });
});

describe('datasource result download (F061 AC-33)', () => {
  const baseAnchor = () => getDatasourceAnchor(DATASOURCE_DETAIL)!;

  it('writes a header row and one line per result row', () => {
    expect(buildDatasourceCsv(baseAnchor()).split('\r\n')).toEqual(['count', '1234']);
  });

  it('quotes a cell containing a comma, quote or newline', () => {
    const anchor = { ...baseAnchor(), columns: ['note'], rows: [['a,b'], ['say "hi"'], ['l1\nl2']] };

    expect(buildDatasourceCsv(anchor).split('\r\n')).toEqual([
      'note',
      '"a,b"',
      '"say ""hi"""',
      '"l1\nl2"',
    ]);
  });

  it('neutralises a formula-looking cell', () => {
    // This file is written from customer database content, so a cell is
    // untrusted input. A spreadsheet would otherwise execute it on open.
    const anchor = { ...baseAnchor(), columns: ['v'], rows: [['=1+1'], ['@SUM(A1)']] };

    const lines = buildDatasourceCsv(anchor).split('\r\n');
    expect(lines[1]).toBe("'=1+1");
    expect(lines[2]).toBe("'@SUM(A1)");
  });

  it('keeps every column when a row is wider than the header', () => {
    // Positional payloads can disagree; dropping the extra cells would lose
    // data the query really returned.
    const anchor = { ...baseAnchor(), columns: ['a'], rows: [[1, 2, 3]] };

    expect(buildDatasourceCsv(anchor).split('\r\n')).toEqual(['a,column_2,column_3', '1,2,3']);
  });

  it('renders null and undefined as empty cells, not the words', () => {
    const anchor = { ...baseAnchor(), columns: ['a', 'b'], rows: [[null, undefined]] };

    expect(buildDatasourceCsv(anchor).split('\r\n')).toEqual(['a,b', ',']);
  });
});
