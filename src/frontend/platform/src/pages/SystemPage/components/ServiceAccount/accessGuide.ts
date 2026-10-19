// F076 access guide: pure helpers behind the 服务账号 → 接入指南 tab.
//
// The guide body (accessGuide.md) carries {{base_url}} / {{ws_base_url}}
// placeholders; BOTH the on-screen render and the exported/copied text go
// through applyGuidePlaceholders below, so "what the admin reads on screen" and
// "what the integrator receives" can never drift apart.
//
// Sync note: the body quotes docs/api/open-api-auth.md for the 8 permission
// scopes and the 11801–11821 error-code table, and src/backend/bisheng/
// open_api/domain/scopes.py is the runtime authority — change one, change all.

declare const __APP_ENV__: { BASE_URL?: string } | undefined

/** localStorage key for the admin-edited access address. */
export const GUIDE_BASE_URL_STORAGE_KEY = "serviceAccount.guideBaseUrl"

const PLACEHOLDER_RE = /\{\{(base_url|ws_base_url)\}\}/g

/**
 * Normalize an admin-entered access address; `null` = not acceptable.
 *
 * Rejected on purpose: empty input, non-http(s) schemes, embedded credentials,
 * query strings and fragments — each of them would render as a broken example
 * URL inside a document we hand to another team. Trailing slashes are dropped;
 * a path prefix (reverse proxies served under /bisheng or /custom) is kept.
 */
export function normalizeAccessBaseUrl(raw: string): string | null {
    const value = raw.trim()
    if (!value) return null
    let url: URL
    try {
        url = new URL(value)
    } catch {
        return null
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null
    if (url.username || url.password || url.search || url.hash) return null
    return `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, "")}`
}

/** ws(s):// twin of the API base — WS handshakes use the same host/port/path. */
export function deriveWsBaseUrl(baseUrl: string): string {
    return baseUrl.replace(/^https:/i, "wss:").replace(/^http:/i, "ws:")
}

/**
 * Default address = the deployment this console is served from. The build-time
 * base path is part of the API path (under a reverse proxy the API lives at
 * origin + BASE_URL + /api/v2/...), so it must not be dropped.
 */
export function defaultGuideBaseUrl(): string {
    const prefix = __APP_ENV__?.BASE_URL ?? ""
    return normalizeAccessBaseUrl(`${window.location.origin}${prefix}`) ?? window.location.origin
}

/** Stored address, or null when absent/invalid — a dirty value never wins over the default. */
export function readStoredGuideBaseUrl(): string | null {
    try {
        const raw = window.localStorage.getItem(GUIDE_BASE_URL_STORAGE_KEY)
        return raw ? normalizeAccessBaseUrl(raw) : null
    } catch {
        return null
    }
}

/** Persist the (already normalized) address; private-mode failures keep it in memory only. */
export function storeGuideBaseUrl(baseUrl: string): void {
    try {
        window.localStorage.setItem(GUIDE_BASE_URL_STORAGE_KEY, baseUrl)
    } catch {
        // Storage unavailable (private mode / quota): the in-memory value is enough.
    }
}

/**
 * The single placeholder substitution point. A function replacer is used so a
 * `$` inside the address is inserted literally, and one regex pass replaces
 * both placeholders (two sequential passes would let `{{base_url}}` match a
 * part of `{{ws_base_url}}`'s replacement).
 */
export function applyGuidePlaceholders(markdown: string, baseUrl: string): string {
    const wsBaseUrl = deriveWsBaseUrl(baseUrl)
    return markdown.replace(PLACEHOLDER_RE, (_match, key: string) => (key === "base_url" ? baseUrl : wsBaseUrl))
}

/**
 * Export/copy text = self-describing header + substituted body. The screen
 * shows the body only; the header exists so a handed-over file states which
 * environment it belongs to and when it was produced.
 */
export function buildGuideText(markdown: string, baseUrl: string, headerLines: string[]): string {
    const header = headerLines.filter((line) => line.trim().length > 0)
    const body = applyGuidePlaceholders(markdown, baseUrl)
    return header.length ? `${header.join("\n")}\n\n${body}` : body
}
