import { getPlatformAdminPanelUrl } from './platformAdminUrl';

type TestAppEnv = Record<string, unknown>;
const testGlobal = globalThis as { __APP_ENV__?: TestAppEnv };

const originalLocation = window.location;
const originalEnv = testGlobal.__APP_ENV__;

function setEnv(platformOrigin: string) {
  testGlobal.__APP_ENV__ = {
    ...(originalEnv ?? {}),
    BISHENG_HOST: '/admin',
    PLATFORM_ORIGIN: platformOrigin,
  };
}

function stubLocation(hostname: string, origin = `http://${hostname}:4001`) {
  Object.defineProperty(window, 'location', {
    value: { origin, hostname },
    writable: true,
    configurable: true,
  });
}

function dropLocation() {
  Object.defineProperty(window, 'location', {
    value: undefined,
    writable: true,
    configurable: true,
  });
}

beforeEach(() => {
  setEnv('');
  stubLocation('localhost');
});

afterEach(() => {
  testGlobal.__APP_ENV__ = originalEnv;
  Object.defineProperty(window, 'location', {
    value: originalLocation,
    writable: true,
    configurable: true,
  });
});

describe('getPlatformAdminPanelUrl platform origin', () => {
  it('keeps the configured loopback origin when the browser is local', () => {
    setEnv('http://127.0.0.1:3001');
    expect(getPlatformAdminPanelUrl()).toBe('http://127.0.0.1:3001/admin');
  });

  it('rewrites a loopback origin to the host the remote browser used', () => {
    setEnv('http://127.0.0.1:3001');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl()).toBe('http://192.168.1.20:3001/admin');
  });

  it('preserves protocol and port when rewriting', () => {
    setEnv('https://localhost:3443');
    stubLocation('admin.internal');
    expect(getPlatformAdminPanelUrl()).toBe('https://admin.internal:3443/admin');
  });

  it('recognises bracketed IPv6 loopback', () => {
    setEnv('http://[::1]:3001');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl()).toBe('http://192.168.1.20:3001/admin');
  });

  it('accepts a configured origin without a scheme', () => {
    setEnv('127.0.0.1:3001');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl()).toBe('http://192.168.1.20:3001/admin');
  });

  it('never rewrites a non-loopback origin', () => {
    setEnv('https://admin.example.com:8443');
    stubLocation('10.0.0.5');
    expect(getPlatformAdminPanelUrl()).toBe('https://admin.example.com:8443/admin');
  });

  it('handles a trailing slash without producing a double slash', () => {
    setEnv('http://127.0.0.1:3001/');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl()).toBe('http://192.168.1.20:3001/admin');
  });

  it('falls back to location.origin when no origin is configured', () => {
    setEnv('');
    stubLocation('10.0.0.5', 'http://10.0.0.5:4001');
    expect(getPlatformAdminPanelUrl()).toBe('http://10.0.0.5:4001/admin');
  });

  it('returns the bare admin path when location and origin are both missing', () => {
    setEnv('');
    dropLocation();
    expect(getPlatformAdminPanelUrl()).toBe('/admin');
  });

  it('does not throw without location when an origin is configured', () => {
    setEnv('http://127.0.0.1:3001');
    dropLocation();
    expect(getPlatformAdminPanelUrl()).toBe('http://127.0.0.1:3001/admin');
  });

  it('passes malformed origins through unchanged', () => {
    setEnv('http://[bad');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl()).toBe('http://[bad/admin');
  });

  it('appends search parameters with a single question mark', () => {
    setEnv('http://127.0.0.1:3001');
    stubLocation('192.168.1.20');
    expect(getPlatformAdminPanelUrl('error=90002')).toBe('http://192.168.1.20:3001/admin?error=90002');
    expect(getPlatformAdminPanelUrl('?error=90002')).toBe('http://192.168.1.20:3001/admin?error=90002');
  });
});
