// F076 access guide: pure helpers behind the 服务账号 → 接入指南 tab.
//
// The guide body (accessGuide.md) carries {{base_url}} / {{ws_base_url}}
// placeholders; BOTH the on-screen render and the exported/copied text go
// through applyGuidePlaceholders below, so "what the admin reads on screen" and
// "what the integrator receives" can never drift apart.
//
// Sync note: the body quotes docs/api/open-api-auth.md for the permission
// scopes and the 118xx/119xx/121xx error-code tables, while src/backend/bisheng/
// open_api/domain/scopes.py and common/errcode/{open_api,scheduled_task,
// managed_agent}.py are the runtime authority — change one, change all.

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
 * Vite's dev flag. Strict files do not get `vite/client` types here (the
 * project has no ambient reference), so `import.meta.env` is read through a
 * narrow cast instead of pulling the global Vite types into every module.
 */
export function isDevServer(): boolean {
    return Boolean((import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV)
}

/** Where the address currently shown came from — surfaced next to the input. */
export type GuideBaseUrlSource = "configured" | "stored" | "detected" | "console"

export interface GuideBaseUrlCandidates {
    /** Ops-configured `open_api.public_base_url`; authoritative when present. */
    configured?: string | null
    /** Backend-detected "http://<host-ip>:<listen-port>"; a guess, dev-only default. */
    detected?: string | null
    /** The admin's saved override for this browser. */
    stored?: string | null
    origin?: string
    prefix?: string
    /** True only on the Vite dev server — the one topology where the console origin is *not* the API entry. */
    isDev?: boolean
}

export interface GuideBaseUrlResolution {
    url: string
    source: GuideBaseUrlSource
}

/**
 * Pick the address shown by default, in priority order:
 *
 *   ops config > admin's saved value > (dev server ? detected : console origin)
 *
 * Ops config wins over the saved value on purpose: a stale per-browser override
 * must not survive a deliberate correction on the deployment.
 *
 * The detected value is only a fallback because a process cannot see its own
 * public address — behind a gateway or TLS terminator the guessed host:port may
 * be unreachable. In production the console's own origin is the documented
 * entry (nginx proxies /api), so it stays the default there.
 */
export function resolveGuideBaseUrl(candidates: GuideBaseUrlCandidates = {}): GuideBaseUrlResolution {
    const prefix = candidates.prefix ?? __APP_ENV__?.BASE_URL ?? ""
    const origin = candidates.origin ?? window.location.origin
    const consoleUrl = normalizeAccessBaseUrl(`${origin}${prefix}`) ?? origin

    const configured = candidates.configured ? normalizeAccessBaseUrl(candidates.configured) : null
    if (configured) return { url: configured, source: "configured" }

    const stored = candidates.stored ? normalizeAccessBaseUrl(candidates.stored) : null
    if (stored) return { url: stored, source: "stored" }

    // The detected host does not carry the reverse-proxy prefix, so append it.
    const detected = candidates.detected ? normalizeAccessBaseUrl(`${candidates.detected}${prefix}`) : null
    if (candidates.isDev && detected) return { url: detected, source: "detected" }

    return { url: consoleUrl, source: "console" }
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
 * Drop the saved override so the next mount resolves from the deployment's own
 * sources again. "Restore default" must not pin the resolved default as a
 * saved override — otherwise the provenance label would read "saved" forever.
 */
export function clearStoredGuideBaseUrl(): void {
    try {
        window.localStorage.removeItem(GUIDE_BASE_URL_STORAGE_KEY)
    } catch {
        // Storage unavailable: nothing was persisted in the first place.
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
