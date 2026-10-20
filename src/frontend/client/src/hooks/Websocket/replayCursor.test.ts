import { isNewerEvent } from './replayCursor';

test('ignores replay duplicates and orders stream IDs numerically', () => {
    expect(isNewerEvent('12-2', '12-1')).toBe(true);
    expect(isNewerEvent('12-2', '12-2')).toBe(false);
    expect(isNewerEvent('9-99', '12-2')).toBe(false);
    expect(isNewerEvent('12345678901234567890-0', '9999999999999999999-0')).toBe(true);
    expect(isNewerEvent('bad', '1-0')).toBe(false);
});
