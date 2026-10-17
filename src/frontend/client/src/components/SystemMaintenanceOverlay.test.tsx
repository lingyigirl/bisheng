import '@testing-library/jest-dom';
import { act, render, screen } from '@testing-library/react';
import { isServiceAvailable } from '~/api/serviceHealth';
import { SERVICE_MAINTENANCE_EVENT, SystemMaintenanceOverlay } from './SystemMaintenanceOverlay';

jest.mock('~/hooks', () => ({ useLocalize: () => (key: string) => key }));
jest.mock('~/api/serviceHealth', () => ({ isServiceAvailable: jest.fn() }));
jest.mock('~/components/illustrations', () => ({ SystemMaintenanceIllustration: () => <div /> }));
const probe = isServiceAvailable as jest.Mock;

beforeEach(() => {
    jest.useFakeTimers();
    probe.mockReset();
});
afterEach(() => jest.useRealTimers());

test('recovers without a reload, probes only while unavailable, and supports another outage', async () => {
    probe.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(false).mockResolvedValue(true);
    render(<SystemMaintenanceOverlay />);
    act(() => { window.dispatchEvent(new Event(SERVICE_MAINTENANCE_EVENT)); });
    expect(screen.getByText('com_app.service_maintenance_title')).toBeInTheDocument();
    for (let i = 0; i < 2; i++) {
        await act(async () => { jest.advanceTimersByTime(5000); });
        expect(screen.getByText('com_app.service_maintenance_title')).toBeInTheDocument();
    }
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(screen.queryByText('com_app.service_maintenance_title')).not.toBeInTheDocument();
    await act(async () => { jest.advanceTimersByTime(20000); });
    expect(probe).toHaveBeenCalledTimes(3);
    act(() => { window.dispatchEvent(new Event(SERVICE_MAINTENANCE_EVENT)); });
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(screen.queryByText('com_app.service_maintenance_title')).not.toBeInTheDocument();
    expect(probe).toHaveBeenCalledTimes(4);
});

test('does not overlap probes and stops scheduling after unmount', async () => {
    let resolveProbe: (available: boolean) => void = () => undefined;
    probe.mockImplementation(() => new Promise<boolean>((resolve) => { resolveProbe = resolve; }));
    const view = render(<SystemMaintenanceOverlay />);
    act(() => { window.dispatchEvent(new Event(SERVICE_MAINTENANCE_EVENT)); });
    await act(async () => { jest.advanceTimersByTime(30000); });
    expect(probe).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => { resolveProbe(false); });
    await act(async () => { jest.advanceTimersByTime(30000); });
    expect(probe).toHaveBeenCalledTimes(1);
});
