/**
 * F076 access guide: the pure helpers behind the 服务账号 → 接入指南 tab.
 *
 * The load-bearing invariant is that the on-screen text and the exported text
 * come from the same substitution, so these tests pin the substitution
 * contract (both placeholders, single pass, `$`-safe) together with the
 * address normalization the exported examples inherit.
 */
import {
    GUIDE_BASE_URL_STORAGE_KEY,
    applyGuidePlaceholders,
    buildGuideText,
    defaultGuideBaseUrl,
    deriveWsBaseUrl,
    normalizeAccessBaseUrl,
    readStoredGuideBaseUrl,
    storeGuideBaseUrl,
} from "@/pages/SystemPage/components/ServiceAccount/accessGuide"
import { beforeEach, describe, expect, it } from "vitest"

// The test setup stubs __APP_ENV__ = { BASE_URL: '' }; this view lets one test
// drive the reverse-proxy prefix case without touching the global stub type.
const globalWithEnv = globalThis as unknown as { __APP_ENV__: { BASE_URL: string } }

describe("accessGuide helpers (F076)", () => {
    beforeEach(() => {
        window.localStorage.clear()
    })

    it("normalizes trailing slashes and keeps reverse-proxy path prefixes", () => {
        expect(normalizeAccessBaseUrl("https://gw.example.com/")).toBe("https://gw.example.com")
        expect(normalizeAccessBaseUrl("http://10.0.0.1:7860///")).toBe("http://10.0.0.1:7860")
        expect(normalizeAccessBaseUrl("https://gw.example.com/bisheng/")).toBe("https://gw.example.com/bisheng")
        expect(normalizeAccessBaseUrl("  HTTPS://GW.Example.com  ")).toBe("https://gw.example.com")
    })

    it("rejects addresses that would render as broken example URLs", () => {
        expect(normalizeAccessBaseUrl("")).toBeNull()
        expect(normalizeAccessBaseUrl("   ")).toBeNull()
        expect(normalizeAccessBaseUrl("10.0.0.1:7860")).toBeNull()
        expect(normalizeAccessBaseUrl("ftp://gw.example.com")).toBeNull()
        expect(normalizeAccessBaseUrl("javascript:alert(1)")).toBeNull()
        expect(normalizeAccessBaseUrl("https://user:pass@gw.example.com")).toBeNull()
        expect(normalizeAccessBaseUrl("https://gw.example.com/?a=1")).toBeNull()
        expect(normalizeAccessBaseUrl("https://gw.example.com/#frag")).toBeNull()
    })

    it("derives the ws(s) twin of the same host, port and path", () => {
        expect(deriveWsBaseUrl("https://gw.example.com/bisheng")).toBe("wss://gw.example.com/bisheng")
        expect(deriveWsBaseUrl("http://10.0.0.1:7860")).toBe("ws://10.0.0.1:7860")
    })

    it("defaults to this deployment's own address, base path included", () => {
        expect(defaultGuideBaseUrl()).toBe(window.location.origin)
        globalWithEnv.__APP_ENV__ = { BASE_URL: "/bisheng" }
        try {
            expect(defaultGuideBaseUrl()).toBe(`${window.location.origin}/bisheng`)
        } finally {
            globalWithEnv.__APP_ENV__ = { BASE_URL: "" }
        }
    })

    it("round-trips the stored address and ignores a dirty stored value", () => {
        expect(readStoredGuideBaseUrl()).toBeNull()
        storeGuideBaseUrl("https://gw.example.com/bisheng/")
        expect(readStoredGuideBaseUrl()).toBe("https://gw.example.com/bisheng")
        window.localStorage.setItem(GUIDE_BASE_URL_STORAGE_KEY, "not a url")
        expect(readStoredGuideBaseUrl()).toBeNull()
    })

    it("substitutes both placeholders in one pass, leaving no residue", () => {
        const md = "a {{base_url}}/api/v2/auth/whoami b {{ws_base_url}}/x c"
        const out = applyGuidePlaceholders(md, "https://gw.example.com")
        expect(out).toBe("a https://gw.example.com/api/v2/auth/whoami b wss://gw.example.com/x c")
        expect(out).not.toContain("{{")
    })

    it("inserts `$` in the address literally (function replacer, not a pattern)", () => {
        expect(applyGuidePlaceholders("{{base_url}}/x", "http://h/$&")).toBe("http://h/$&/x")
    })

    it("builds export text as header lines plus the substituted body", () => {
        expect(buildGuideText("# Doc {{base_url}}", "http://h:1", ["> src", "> at now"])).toBe(
            "> src\n> at now\n\n# Doc http://h:1",
        )
        expect(buildGuideText("# Doc", "http://h:1", ["   "])).toBe("# Doc")
    })
})
