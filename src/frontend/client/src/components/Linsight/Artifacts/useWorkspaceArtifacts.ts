/**
 * F035: session-level artifact aggregation for the inline workspace panel.
 *
 * The panel used to render ONLY the latest task turn's files. Each follow-up
 * round is a new session_version, and the backend deliberately does not
 * re-deliver inherited-but-unchanged files — so the newest round's `file_list`
 * holds only what that round produced or rewrote, and every earlier
 * deliverable (pptx / xlsx / md) disappeared from the panel even though its
 * own turn card still offered it.
 *
 * This hook walks every task turn of the conversation in chronological order,
 * merges uploads + deliverables via mergeWorkspaceArtifacts (later rounds win
 * on same-named files), and stamps every entry with its owning version id so
 * preview / download resolve against the right session version.
 *
 * Rounds not hydrated in the store yet (fresh page load, or the panel opened
 * before the turn panels finished loading) are fetched once the panel opens —
 * the same session-version-list + execute-task-detail pair TaskTurnPanel uses.
 */
import { useEffect, useMemo, useRef } from 'react';
import { getLinsightSessionVersionList, getLinsightTaskList } from '~/api/linsight';
import { useLinsightManager } from '~/hooks/useLinsightManager';
import type { LinsightInfo } from '~/store/linsight';
import {
    type ArtifactFile,
    mergeWorkspaceArtifacts,
    type WorkspaceArtifactRound,
} from './artifactUtils';

/**
 * @param conversationId   chat id owning the task turns ('' / 'new' → nothing
 *                         to aggregate)
 * @param versionIds       task turns' session_version ids, oldest → newest
 * @param enabled          hydrate missing rounds only while the panel is open
 *                         (avoids per-conversation API chatter for users who
 *                         never look at the file list)
 * @param shareToken       share-route token so recipients can hydrate too
 */
export function useWorkspaceArtifacts(
    conversationId: string,
    versionIds: string[],
    enabled: boolean,
    shareToken = '',
): ArtifactFile[] {
    const { linsightMap, switchAndUpdateLinsight } = useLinsightManager();

    // Live callback ref: the hydration effect must not re-run just because the
    // store map changed (switchAndUpdateLinsight's identity depends on it).
    const hydrateRef = useRef(switchAndUpdateLinsight);
    hydrateRef.current = switchAndUpdateLinsight;

    // In-flight guard shared across effect runs — reopening the panel while a
    // fetch is underway must not start a second one.
    const inFlightRef = useRef<Set<string>>(new Set());

    // `versionIds` is rebuilt on every message-store change; join it into a
    // primitive so the effect below only reruns on real membership changes.
    const versionKey = versionIds.join('|');

    useEffect(() => {
        if (!enabled || !conversationId || conversationId === 'new') return;
        const missing = versionIds.filter(
            (vid) => vid && !linsightMap.has(vid) && !inFlightRef.current.has(vid),
        );
        if (!missing.length) return;
        missing.forEach((vid) => inFlightRef.current.add(vid));
        let cancelled = false;
        (async () => {
            try {
                const versions: LinsightInfo[] = await getLinsightSessionVersionList(
                    conversationId,
                    shareToken,
                );
                const byId = new Map(
                    (Array.isArray(versions) ? versions : []).map((v) => [v.id, v] as const),
                );
                for (const vid of missing) {
                    const item = byId.get(vid);
                    if (!item) continue;
                    const tasks = await getLinsightTaskList(vid, item, shareToken);
                    if (cancelled) return;
                    hydrateRef.current(vid, { ...item, tasks });
                }
            } catch (e) {
                console.error('[useWorkspaceArtifacts] failed to load round detail:', e);
            } finally {
                missing.forEach((vid) => inFlightRef.current.delete(vid));
            }
        })();
        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps -- versionIds joined into versionKey; live values are read via refs / the store map
    }, [enabled, conversationId, versionKey, shareToken]);

    return useMemo(() => {
        const rounds: WorkspaceArtifactRound[] = [];
        for (const vid of versionIds) {
            if (!vid) continue;
            const info = linsightMap.get(vid);
            if (!info) continue;
            // Rounds folded into `history` by the same-SV continuation path
            // (legacy full-page flow) belong to this version too.
            for (const round of info.history || []) {
                rounds.push({ versionId: vid, generated: round?.file_list });
            }
            rounds.push({
                versionId: vid,
                uploaded: info.files,
                generated: info.file_list,
            });
        }
        return mergeWorkspaceArtifacts(rounds);
        // linsightMap identity changes on every store write — the real signal
        // that a round hydrated or new files arrived.
    }, [linsightMap, versionIds]);
}
