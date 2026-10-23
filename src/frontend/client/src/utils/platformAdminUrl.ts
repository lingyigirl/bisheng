/**
 * 本地开发：工作台在 :4001、管理端在 :3001，相对路径 /admin 会错误地指向 4001。
 * 生产：通常同源反代，用当前 origin + BISHENG_HOST 即可。
 */

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1']);

/** URL.hostname keeps brackets on IPv6 literals, e.g. "[::1]". */
function isLoopbackHostname(hostname: string): boolean {
  return LOOPBACK_HOSTS.has(hostname.replace(/^\[|\]$/g, '').toLowerCase());
}

/**
 * The tracked dev default for PLATFORM_ORIGIN is loopback. A remote browser
 * would resolve that to its own machine, so when this page is not served from
 * loopback, swap in the host the browser actually used. Protocol and port are
 * preserved; every other configured value is returned verbatim.
 */
function resolveConfiguredOrigin(platformOrigin: string): string {
  if (!platformOrigin) return '';

  const currentHostname =
    typeof location !== 'undefined' && typeof location.hostname === 'string' ? location.hostname : '';
  if (!currentHostname || isLoopbackHostname(currentHostname)) return platformOrigin;

  // The env value may omit the scheme (e.g. "127.0.0.1:3001").
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(platformOrigin)
    ? platformOrigin
    : `http://${platformOrigin}`;

  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    // Malformed value: keep the previous pass-through behavior.
    return platformOrigin;
  }
  if (!isLoopbackHostname(parsed.hostname)) return platformOrigin;

  const port = parsed.port ? `:${parsed.port}` : '';
  return `${parsed.protocol}//${currentHostname}${port}`;
}

export function getPlatformAdminPanelUrl(search?: string): string {
  const path = __APP_ENV__.BISHENG_HOST || '/admin';
  const configuredOrigin =
    (__APP_ENV__ as { PLATFORM_ORIGIN?: string }).PLATFORM_ORIGIN?.replace(/\/$/, '') || '';
  const platformOrigin = resolveConfiguredOrigin(configuredOrigin);
  const origin = platformOrigin || (typeof location !== 'undefined' ? location.origin : '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  let url = `${origin}${suffix}`;
  if (search) {
    url += search.startsWith('?') ? search : `?${search}`;
  }
  return url;
}
