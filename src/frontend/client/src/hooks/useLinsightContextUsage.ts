import { useEffect, useRef, useState } from 'react';
import { getLinsightContextUsage } from '~/api/linsight';
import type { ContextUsageSnapshot } from '~/types/contextUsage';

/** Coarse enough to be cheap, fine enough to feel live. */
const POLL_INTERVAL = 5000;

// Module-level cache + in-flight dedupe. A conversation with N task turns
// otherwise issues N requests on every mount pass; with this, each version is
// fetched at most once (remounts / tab switches reuse the cache) and concurrent
// callers share one request. Entries are tiny (a few numbers) and page-scoped.
const usageCache = new Map<string, ContextUsageSnapshot>();
const inflight = new Map<string, Promise<ContextUsageSnapshot | null>>();

function fetchUsage(versionId: string): Promise<ContextUsageSnapshot | null> {
    const running = inflight.get(versionId);
    if (running) return running;
    const request = getLinsightContextUsage(versionId)
        .then((res) => {
            const data = res?.data;
            if (data?.main) {
                usageCache.set(versionId, data);
                return data;
            }
            return null;
        })
        .catch(() => null)
        .finally(() => inflight.delete(versionId));
    inflight.set(versionId, request);
    return request;
}

/**
 * F086 context display: fetch the task run's token snapshot.
 *
 * While the run is live (``enabled``) the endpoint is polled every 5s so the
 * token row keeps up with the growing context; once the run is over one final
 * fetch seeds completed/history turns (the worker keeps the snapshot in Redis
 * for 7 days). The row is non-critical UI: a failed poll is retried on the next
 * tick — never surfaced.
 *
 * @param versionId linsight session_version id of the turn
 * @param enabled   poll while the turn is running (status === Running)
 */
export function useLinsightContextUsage(versionId: string, enabled: boolean): ContextUsageSnapshot | null {
    const [usage, setUsage] = useState<ContextUsageSnapshot | null>(() => usageCache.get(versionId) ?? null);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // A reused component instance switching turns: read the cache immediately.
    useEffect(() => {
        setUsage(usageCache.get(versionId) ?? null);
    }, [versionId]);

    useEffect(() => {
        if (!versionId || versionId === 'new') return;
        // Done + cached (history turn remount, live→done transition): no network.
        if (!enabled && usageCache.has(versionId)) {
            setUsage(usageCache.get(versionId)!);
            return;
        }
        let cancelled = false;

        const tick = async () => {
            // Keep the previous value when the backend has nothing (queued /
            // expired / pre-feature run) — the component self-gates on data.
            const data = await fetchUsage(versionId);
            if (!cancelled && data) setUsage(data);
            if (!cancelled && enabled) timerRef.current = setTimeout(tick, POLL_INTERVAL);
        };
        tick();

        return () => {
            cancelled = true;
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [versionId, enabled]);

    return usage;
}
