/**
 * F063 — the organization-knowledge-base "tier" (全部 / 不使用) as pure functions.
 *
 * Why a marker item instead of an atom: Recoil is frozen (client `AGENTS.md` —
 * no new atoms, no new `recoil` imports), so the tier lives as a synthetic entry
 * inside the existing `selectedOrgKbs` atom (design 决策 6). Keeping every
 * decision here — Recoil-free — is also what makes it unit-testable.
 *
 * 坑 13: the marker's `type` is `'org'`, the same as a real organization KB
 * entry. Anything that selects org entries with `type === 'org'`, or drops them
 * with `type !== 'org'`, will silently include or destroy the user's tier.
 * `isOrgScopeMarker` (id-based) is the only sanctioned discriminator.
 *
 * No `recoil` import may ever be added to this module.
 */

import type { OrgScope, SelectedOrgKb } from '~/store/modeltype';

/** Sentinel id of the marker entry. Addressed by nothing else — keep it here. */
export const ORG_SCOPE_MARKER_ID = '__org_scope__';

/**
 * The entry the summary card hands back when the user dismisses it (AC-16).
 *
 * Only the id is load-bearing (`isOrgScopeMarker` keys off it); the shape is
 * kept complete so it is interchangeable with a real marker everywhere else.
 * It exists because the marker is *not* always in the state — the AC-02
 * fallback resolves to `all` with no marker present at all, and that is the
 * state the card is most often dismissed from.
 */
export const ORG_SCOPE_MARKER: SelectedOrgKb = {
  id: ORG_SCOPE_MARKER_ID,
  name: '',
  type: 'org',
};

const isOrgScopeValue = (value: unknown): value is OrgScope => value === 'all' || value === 'none';

/** True for the tier marker — and false for a real org KB entry (坑 13). */
export function isOrgScopeMarker(item: unknown): boolean {
  return (
    !!item &&
    typeof item === 'object' &&
    (item as { id?: unknown }).id === ORG_SCOPE_MARKER_ID
  );
}

/**
 * The tier the user explicitly set, or `undefined` when never set.
 *
 * A marker carrying an illegal value (dirty data from an older/newer client) is
 * reported as "never set" rather than coerced — guessing a tier would invent a
 * decision the user did not make.
 */
export function readOrgScope(items: SelectedOrgKb[] | null | undefined): OrgScope | undefined {
  if (!Array.isArray(items)) return undefined;
  const found = items.find(isOrgScopeMarker);
  return isOrgScopeValue(found?.orgScope) ? found?.orgScope : undefined;
}

/** True when the selection contains at least one knowledge space. */
export function hasKnowledgeSpaces(items: SelectedOrgKb[] | null | undefined): boolean {
  return Array.isArray(items) && items.some((item) => item?.type === 'space');
}

/**
 * The effective tier. An explicit choice always wins and never bounces back
 * when spaces are added or cleared; without one, the fallback answers AC-02:
 * "I only picked knowledge spaces" means "not the org KBs this time".
 */
export function resolveOrgScope(
  items: SelectedOrgKb[] | null | undefined,
  hasSpaces: boolean,
): OrgScope {
  return readOrgScope(items) ?? (hasSpaces ? 'none' : 'all');
}

/**
 * Normalise what was read off localStorage (AC-21, 决策 8).
 *
 * Only knowledge-space entries and a legal marker survive; legacy per-item
 * organization entries are dropped rather than migrated — the old code allowed
 * clearing the list to `[]`, so "was he using org KBs?" is genuinely
 * undecidable from that data, and encoding a rule around an undecidable signal
 * would be encoding uncertainty. A dropped list is indistinguishable from "never
 * set", so the fallback decides.
 *
 * Never mutates its input: the value is also read by other consumers.
 */
export function normalizeStoredItems(raw: unknown): SelectedOrgKb[] {
  if (!Array.isArray(raw)) return [];
  const items: SelectedOrgKb[] = [];
  let markerTaken = false;
  for (const entry of raw) {
    if (isOrgScopeMarker(entry)) {
      // One marker at most: duplicates would make the tier depend on order.
      if (markerTaken) continue;
      const scope = (entry as SelectedOrgKb).orgScope;
      if (!isOrgScopeValue(scope)) continue;
      markerTaken = true;
      items.push({ id: ORG_SCOPE_MARKER_ID, name: '', type: 'org', orgScope: scope });
      continue;
    }
    const space = entry as SelectedOrgKb | null;
    // A space entry's id is its identity AND goes into the request body, so a
    // malformed one is dropped here rather than becoming `Number(undefined)` →
    // NaN → a 422 on the whole turn. This is the untrusted-disk boundary.
    if (space?.type === 'space' && typeof space.id === 'string' && space.id.length > 0) {
      items.push(space);
    }
  }
  return items;
}

/** Drop the marker, keeping every other entry and its position. */
export function withoutOrgScopeMarker<T extends SelectedOrgKb>(items: T[]): T[] {
  return (Array.isArray(items) ? items : []).filter((item) => !isOrgScopeMarker(item));
}

/**
 * Keep the tier marker and nothing else — the counterpart of
 * `withoutOrgScopeMarker`, for the legacy mutex that clears the picked KBs when
 * files are added or a tool is switched on.
 *
 * That mutex means "this turn is about files, not about the KBs he picked" — it
 * is not a statement about the tier, which is a standing setting rather than a
 * pick. Clearing the whole array would drop the marker, the AC-02 fallback would
 * answer again, and a user who chose 「不使用」 would silently get 「全部」: the
 * exact re-opening AC-07/AC-16 forbid. An empty result is correct and intended
 * when no tier was ever set — writing one here would turn a fallback into a
 * decision the user never made.
 */
export function onlyOrgScopeMarker<T extends SelectedOrgKb>(items: T[]): T[] {
  return (Array.isArray(items) ? items : []).filter(isOrgScopeMarker);
}

/**
 * True when the selection holds at least one real entry — the counterpart of
 * `isOrgScopeAll` ("this turn searches all org KBs").
 *
 * Deliberately not `items.length > 0`: the tier marker is a state carrier, not a
 * picked KB, so a raw length reports "a KB was picked" for a user who explicitly
 * chose 「不使用」 (坑 13). Callers use this to derive `org_knowledge_enabled` /
 * `useKnowledgeBase`, where that mistake is a user-visible misreport.
 */
export function hasPickedKnowledge(items: SelectedOrgKb[] | null | undefined): boolean {
  return Array.isArray(items) && items.some((item) => !isOrgScopeMarker(item));
}

/**
 * Write (or overwrite) the tier. The only writer, so the marker cannot pile up.
 *
 * Generic over the entry type: the tier is carried by the store's `SelectedOrgKb`
 * as well as by the input's `KnowledgeItem` (its `type` is the narrower
 * `'org' | 'space'`, which still satisfies `SelectedOrgKb`), and both must get
 * their own type back out of the same helper.
 */
export function withOrgScope<T extends SelectedOrgKb>(items: T[], scope: OrgScope): T[] {
  return [
    ...withoutOrgScopeMarker(items),
    // The marker is the one entry the two shapes agree on — `id` / `name` /
    // `type` (plus the tier) is all either of them carries.
    { id: ORG_SCOPE_MARKER_ID, name: '', type: 'org', orgScope: scope } as unknown as T,
  ];
}

/**
 * The tier resolves to 「全部」 *and* the feature is on — i.e. "this turn searches
 * all organization knowledge bases". The count is deliberately not part of it:
 * this is the same condition the request payload sends under (see
 * `toPayloadKnowledgeScope`), so every surface that reports "org KBs are in
 * play" agrees with what is actually asked of the server.
 */
export function isOrgScopeAll(
  items: SelectedOrgKb[] | null | undefined,
  enabled: boolean,
): boolean {
  return enabled && resolveOrgScope(items, hasKnowledgeSpaces(items)) === 'all';
}

/**
 * Whether the org tier should be represented on the attachment bar (AC-15).
 *
 * Shared by the bar (does it render the card) and by the input that mounts the
 * bar (is the card on its own a reason for the strip to exist) — the two must
 * agree, or the default state renders either nothing or an empty strip. Unlike
 * `isOrgScopeAll` this one NEEDS the count: AC-15 withholds the card when the
 * user has no org KB at all, so the card never claims to search nothing.
 */
export function showsOrgKbSummary(
  items: SelectedOrgKb[] | null | undefined,
  enabled: boolean,
  kbCount: number,
): boolean {
  return kbCount > 0 && isOrgScopeAll(items, enabled);
}

/**
 * Apply a removal coming from the attachment bar (AC-16).
 *
 * For a knowledge space that means "drop that entry". For the tier card it must
 * NOT mean the same thing: dropping the marker returns the state to "never set",
 * the fallback answers `all` again and the card the user just dismissed is back
 * on the next render (坑 4). Removal therefore writes the opposite tier.
 *
 * `target` is whatever the card carried — treat it as untrusted.
 */
export function removeSelectedOrgKb<T extends SelectedOrgKb>(items: T[], target: unknown): T[] {
  if (isOrgScopeMarker(target)) return withOrgScope(items, 'none');
  const targetId = (target as SelectedOrgKb | null | undefined)?.id;
  return items.filter((item) => item?.id !== targetId);
}

export type KnowledgeScopePayload = {
  organization_knowledge_all: boolean;
  organization_knowledge_ids: number[];
  knowledge_space_ids: number[];
};

/**
 * The request-body fragment for `use_knowledge_base`.
 *
 * The two org forms are mutually exclusive (spec AC-05): when the all-tier is
 * claimed, the explicit id list must stay empty. `featureEnabled` is the admin
 * switch — when it is off the client must not ask the server to expand (AC-18);
 * knowledge spaces are a separate subsystem and are unaffected by it.
 */
export function toPayloadKnowledgeScope(
  items: SelectedOrgKb[],
  hasSpaces: boolean,
  featureEnabled: boolean,
): KnowledgeScopePayload {
  const scope = resolveOrgScope(items, hasSpaces);
  return {
    organization_knowledge_all: featureEnabled && scope === 'all',
    organization_knowledge_ids: [],
    knowledge_space_ids: withoutOrgScopeMarker(items)
      .filter((item) => item?.type === 'space')
      .map((item) => Number(item.id)),
  };
}
