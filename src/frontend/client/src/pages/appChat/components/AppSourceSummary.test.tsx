/**
 * F061 T-40: the app's source summary above the input.
 *
 * The load-bearing assertion is the ABSENCE: an app with no datasource bound —
 * which is every app in the install base today — must render nothing at all, not
 * a "数据源:" row with nothing after it. An empty labelled row reads as a
 * configured-but-broken source, and it would appear on every app in the product.
 */

import { render, screen } from '@testing-library/react';
import { AppSourceSummary } from './AppSourceSummary';

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
}));

const DATASOURCE_NAME = String.fromCharCode(0x5ba2, 0x6237, 0x5e93);
const OTHER_NAME = String.fromCharCode(0x62a5, 0x8868, 0x5e93);

describe('AppSourceSummary (F061 T-40)', () => {
  it('renders nothing when the app binds no datasource', () => {
    const { container } = render(<AppSourceSummary datasources={[]} />);

    expect(container.innerHTML).toBe('');
  });

  it('names the datasources the app may draw on', () => {
    render(<AppSourceSummary datasources={[DATASOURCE_NAME]} />);

    expect(screen.getByText(DATASOURCE_NAME)).toBeTruthy();
    expect(screen.getByText('com_citation_source_datasource')).toBeTruthy();
  });

  it('lists every bound datasource, not just the first', () => {
    render(<AppSourceSummary datasources={[DATASOURCE_NAME, OTHER_NAME]} />);

    // Rendered as one joined string so a long list truncates with a title rather
    // than wrapping and pushing the input box down.
    expect(screen.getByText(`${DATASOURCE_NAME}, ${OTHER_NAME}`)).toBeTruthy();
  });
});
