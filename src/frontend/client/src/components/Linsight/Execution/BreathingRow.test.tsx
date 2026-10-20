import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';
import { BreathingRow } from './BreathingRow';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));

test('preparation feedback advances before model frames and stops ticking on unmount', () => {
    jest.useFakeTimers();
    const { unmount } = render(<BreathingRow state="planning" />);
    expect(screen.getByText('com_linsight_planning · 0s')).toBeVisible();
    act(() => jest.advanceTimersByTime(3000));
    expect(screen.getByText('com_linsight_planning · 3s')).toBeVisible();
    unmount();
    expect(jest.getTimerCount()).toBe(0);
    jest.useRealTimers();
});
