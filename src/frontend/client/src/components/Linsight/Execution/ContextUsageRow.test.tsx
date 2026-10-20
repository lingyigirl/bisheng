import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { ContextUsageRow, formatTokenCount } from './ContextUsageRow';

// Render the key plus its interpolation params so assertions can read them.
jest.mock('~/hooks', () => ({
    useLocalize: () => (key: string, opts?: Record<string, unknown>) =>
        opts ? `${key} ${Object.entries(opts).map(([k, v]) => `${k}=${v}`).join(' ')}` : key,
}));

test('renders the token summary once a main-graph call has happened', () => {
    render(
        <ContextUsageRow
            usage={{
                main: { calls: 3, cum_input_tokens: 12000, cum_output_tokens: 450, last_input_tokens: 5100 },
                sub: { calls: 1 },
                breakdown: { system_chars: 1000, history_chars: 3000, tool_schema_chars: 1000 },
            }}
        />,
    );

    const row = screen.getByTestId('context-usage');
    expect(row).toHaveTextContent('com_linsight_context_summary');
    expect(row).toHaveTextContent('tokens=5.1K'); // newest main call == current context
    expect(row).toHaveTextContent('input=12K');
    expect(row).toHaveTextContent('output=450');
    expect(row).toHaveTextContent('calls=3');
    expect(row).toHaveTextContent('sub=1');

    // Character-ratio composition rides on the native tooltip.
    const title = row.getAttribute('title') || '';
    expect(title).toContain('com_linsight_context_breakdown');
    expect(title).toContain('system=20');
    expect(title).toContain('history=60');
    expect(title).toContain('tools=20');
});

test('renders nothing before the first main call', () => {
    const { container } = render(<ContextUsageRow usage={{}} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('context-usage')).toBeNull();
});

test('omits the breakdown tooltip when no composition estimate exists', () => {
    render(<ContextUsageRow usage={{ main: { calls: 1, last_input_tokens: 900 } }} />);
    expect(screen.getByTestId('context-usage')).not.toHaveAttribute('title');
});

test('compact units: K below a million, M above, no 1000K, raw below 1000', () => {
    expect(formatTokenCount(482)).toBe('482');
    expect(formatTokenCount(999)).toBe('999');
    expect(formatTokenCount(1000)).toBe('1K');
    expect(formatTokenCount(10_082)).toBe('10.1K');
    expect(formatTokenCount(12_000)).toBe('12K');
    expect(formatTokenCount(128_000)).toBe('128K');
    expect(formatTokenCount(999_449)).toBe('999K');
    expect(formatTokenCount(999_949)).toBe('1M'); // promotes instead of "1000K"
    expect(formatTokenCount(1_234_567)).toBe('1.2M');
    expect(formatTokenCount(null)).toBe('0');
    expect(formatTokenCount(undefined)).toBe('0');
});
