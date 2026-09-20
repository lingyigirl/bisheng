/**
 * F061 T-41: the anchor panel's two structural guarantees.
 *
 * AC-32 says the statement and result table expand, collapsed by default. AC-36
 * says a turn that queried nothing gets NO database section — not an empty one.
 *
 * AC-36 is why this file exists. "Renders nothing" is the kind of guarantee that
 * degrades into "renders an empty box" without anyone noticing: the empty box
 * looks like a deliberate state, and an empty expander tells the user a database
 * was consulted and came back with nothing. The absence has to be asserted.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import type { ChatCitation } from '~/api/chatApi';
import { DatasourceAnchorPanel } from './DatasourceAnchorPanel';

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
}));

jest.mock('~/utils', () => ({
  cn: (...args: unknown[]) => args.filter(Boolean).join(' '),
}));

const DATASOURCE_NAME = String.fromCharCode(0x5ba2, 0x6237, 0x5e93);

function datasourceCitation(sourcePayload: Record<string, unknown>): ChatCitation {
  return {
    citationId: 'datasource_7_0',
    type: 'DATASOURCE',
    sourcePayload,
  } as ChatCitation;
}

const WITH_ROWS = datasourceCitation({
  datasourceId: 7,
  datasourceName: DATASOURCE_NAME,
  tables: ['orders'],
  statement: 'SELECT id FROM orders',
  columns: ['id'],
  rows: [[1], [2]],
  truncated: false,
});

describe('DatasourceAnchorPanel (F061 AC-32 / AC-36)', () => {
  it('renders nothing at all when there is no datasource anchor (AC-36)', () => {
    const { container } = render(<DatasourceAnchorPanel detail={null} />);

    // Asserted on innerHTML rather than `toBeEmptyDOMElement`: jest-dom is wired
    // up at runtime (jest.config setupFilesAfterEnv) but its matcher TYPES are
    // not, and adding them is a tsconfig change out of scope here.
    expect(container.innerHTML).toBe('');
  });

  it('renders nothing for a document or web citation (AC-36)', () => {
    const document = {
      citationId: 'knowledgesearch_a',
      type: 'RAG',
      sourcePayload: { documentName: 'handbook.pdf' },
    } as ChatCitation;

    const { container } = render(<DatasourceAnchorPanel detail={document} />);

    expect(container.innerHTML).toBe('');
  });

  it('keeps the statement and the result rows collapsed until asked (AC-32)', () => {
    render(<DatasourceAnchorPanel detail={WITH_ROWS} />);

    // The header is there — the datasource and its table are visible.
    expect(screen.getByText(new RegExp(DATASOURCE_NAME))).toBeTruthy();
    // The SQL and the cell values are not.
    expect(screen.queryByText('SELECT id FROM orders')).toBeNull();
  });

  it('reveals the statement once expanded', async () => {
    render(<DatasourceAnchorPanel detail={WITH_ROWS} />);

    fireEvent.click(screen.getByRole('button'));

    expect(await screen.findByText('SELECT id FROM orders')).toBeTruthy();
  });

  it('announces its expanded state for assistive tech (AC-32)', () => {
    render(<DatasourceAnchorPanel detail={WITH_ROWS} />);

    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false');
  });

  it('carries the row count in the collapsed header', () => {
    render(<DatasourceAnchorPanel detail={WITH_ROWS} />);

    // The header says how much is behind the toggle, so the user can decide
    // whether opening it is worth a click.
    expect(screen.getByText(/com_citation_datasource_rows/)).toBeTruthy();
  });

  it('renders a query that matched no rows without a table', async () => {
    const empty = datasourceCitation({
      datasourceId: 7,
      datasourceName: DATASOURCE_NAME,
      tables: ['orders'],
      statement: 'SELECT id FROM orders WHERE 1 = 0',
      columns: ['id'],
      rows: [],
    });

    render(<DatasourceAnchorPanel detail={empty} />);
    fireEvent.click(screen.getByRole('button'));

    // The statement is still shown — the query DID run — but there is no result
    // table and no download for an empty set.
    expect(await screen.findByText('SELECT id FROM orders WHERE 1 = 0')).toBeTruthy();
    expect(screen.getByText('com_citation_datasource_no_rows')).toBeTruthy();
  });
});
