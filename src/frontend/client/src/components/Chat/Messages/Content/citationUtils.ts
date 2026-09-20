import type { ChatCitation } from '~/api/chatApi';
import { getFilePathApi } from '~/api/chat/data-service';

export type CitationDisplayData = {
  label: number;
  ref: string;
  type: string;
  groupKey: string;
  chunkId: string;
  citationId: string;
  itemId: string;
};

export type CitationPreview = {
  title: string;
  snippet: string;
  sourceName: string;
  sourceMeta: string;
  link?: string;
  type: string;
};

export type CitationReferenceItem = {
  key: string;
  data: CitationDisplayData;
  detail?: ChatCitation | null;
  legacyPreview?: CitationPreview | null;
};

export type CitationPdfBBox = {
  page: number;
  bbox: [number, number, number, number];
};

export type CitationDetailLoader = (citationId: string) => Promise<ChatCitation | null>;

export const CITATION_START = '\ue200';
export const CITATION_SEPARATOR = '\ue201';
export const CITATION_END = '\ue202';

/**
 * F061: the backend's `CitationType.DATASOURCE`. It arrives UPPERCASED (the
 * enum's value), so every comparison goes through `isDatasourceCitation`
 * rather than a literal \u2014 spelled twice it would drift from the backend.
 */
export const DATASOURCE_CITATION_TYPE = 'DATASOURCE';

export function normalizeCitationMarkers(content: string) {
  return content.replace(/(\\+)u([eE]20[012])/g, (match, slashes, code) => {
    if (slashes.length % 2 !== 0) {
      const prefix = slashes.slice(1);
      const marker = code.toLowerCase();
      if (marker === 'e200') return prefix + CITATION_START;
      if (marker === 'e201') return prefix + CITATION_SEPARATOR;
      if (marker === 'e202') return prefix + CITATION_END;
    }
    return match;
  });
}

/**
 * Remove citation marker groups (...) from a string. Used when
 * exporting message text to plain contexts like clipboard copy where the
 * private-use markers have no rendered counterpart and would surface as
 * unreadable noise.
 */
export function stripCitationMarkers(content: string) {
  if (!content) return content;
  return content
    // Strip well-formed groups first (non-greedy across separators).
    .replace(/[\s\S]*?/g, '')
    // Also handle the escaped form ("...") that may slip through
    // when a message hasn't been passed through normalizeCitationMarkers.
    .replace(/\\u[eE]200[\s\S]*?\\u[eE]202/g, '')
    // Drop any orphan markers (e.g. a streaming-truncated group).
    .replace(/[]/g, '');
}

function padTimeUnit(value: number) {
  return String(value).padStart(2, '0');
}

export function formatCitationWebDate(value?: string | null) {
  const rawValue = String(value || '').trim();
  if (!rawValue) {
    return '';
  }

  const normalizedValue = rawValue.replace(/\//g, '-');
  const parsedDate = new Date(normalizedValue);
  if (Number.isNaN(parsedDate.getTime())) {
    return rawValue;
  }

  return `${parsedDate.getFullYear()}-${padTimeUnit(parsedDate.getMonth() + 1)}-${padTimeUnit(parsedDate.getDate())} ${padTimeUnit(parsedDate.getHours())}:${padTimeUnit(parsedDate.getMinutes())}`;
}

function parseCitationRef(ref: string) {
  const lastColonIndex = ref.lastIndexOf(':');
  if (lastColonIndex < 0) {
    return null;
  }

  const citationId = ref.slice(0, lastColonIndex).trim();
  const itemId = ref.slice(lastColonIndex + 1).trim();

  if (!citationId || !itemId) {
    return null;
  }

  const firstUnderscoreIndex = citationId.indexOf('_');
  if (firstUnderscoreIndex < 0) {
    return null;
  }

  const type = citationId.slice(0, firstUnderscoreIndex).trim();
  const groupKey = citationId.slice(firstUnderscoreIndex + 1).trim();

  if (!type || !groupKey) {
    return null;
  }

  return {
    type,
    groupKey,
    chunkId: itemId,
    citationId,
    itemId,
  };
}

export function buildCitationDisplayData(ref: string): CitationDisplayData | null {
  const parsedRef = parseCitationRef(ref);
  if (!parsedRef) {
    return null;
  }

  const { citationId, itemId, type, groupKey, chunkId } = parsedRef;

  return {
    label: 0,
    ref,
    type,
    groupKey,
    chunkId,
    citationId,
    itemId,
  };
}

export function transformPrivateCitations(content: string) {
  const normalizedCitationContent = normalizeCitationMarkers(content);

  if (!normalizedCitationContent.includes(CITATION_START)) {
    return { transformedContent: normalizedCitationContent, citationMap: {} as Record<string, CitationDisplayData> };
  }

  const citationMap: Record<string, CitationDisplayData> = {};
  const groupIndexMap: Record<string, number> = {};
  let nextGroupLabel = 1;
  const buildCitationPlaceholder = (ref: string) => {
    const normalizedRef = ref.trim();
    if (!normalizedRef) {
      return '';
    }

    if (!citationMap[normalizedRef]) {
      const displayData = buildCitationDisplayData(normalizedRef);
      if (displayData) {
        const groupId = `${displayData.type}_${displayData.groupKey}_${displayData.itemId}`;
        if (!groupIndexMap[groupId]) {
          groupIndexMap[groupId] = nextGroupLabel;
          nextGroupLabel += 1;
        }
        displayData.label = groupIndexMap[groupId];
        citationMap[normalizedRef] = displayData;
      }
    }

    return citationMap[normalizedRef] ? `[citationref:${normalizedRef}]` : '';
  };

  let transformedContent = '';
  let currentRef = '';
  let isInsideCitation = false;

  for (const char of normalizedCitationContent) {
    if (!isInsideCitation) {
      if (char === CITATION_START) {
        isInsideCitation = true;
        currentRef = '';
        continue;
      }
      transformedContent += char;
      continue;
    }

    if (char === CITATION_SEPARATOR) {
      transformedContent += buildCitationPlaceholder(currentRef);
      currentRef = '';
      continue;
    }

    if (char === CITATION_END) {
      transformedContent += buildCitationPlaceholder(currentRef);
      currentRef = '';
      isInsideCitation = false;
      continue;
    }

    currentRef += char;
  }

  return { transformedContent, citationMap };
}

export function getCitationClassName(type?: string) {
  switch (type?.toLowerCase()) {
    case 'web':
    case 'websearch':
      return 'bg-[#F7F3FF] text-[#7224D9] transition-colors duration-150 hover:bg-[#EDE4FF] data-[state=open]:bg-[#EDE4FF]';
    case 'knowledgesearch':
      return 'bg-blue-50 text-blue-600 transition-colors duration-150 hover:bg-blue-100 data-[state=open]:bg-blue-100';
    default:
      return 'bg-blue-50 text-blue-600 transition-colors duration-150 hover:bg-blue-100 data-[state=open]:bg-blue-100';
  }
}

/**
 * F061: is this a database anchor?
 *
 * The three-kind split exists because the two-kind default is wrong for a
 * datasource: `normalizeCitationType` returns 'rag' for anything non-web, so a
 * database anchor was labelled 文档 and sent down the document path, where its
 * statement, table list and result rows have nowhere to render.
 */
export function isDatasourceCitation(detail?: Pick<ChatCitation, 'type'> | null) {
  return String(detail?.type || '').toLowerCase() === 'datasource';
}

export function normalizeCitationType(type?: string) {
  const normalizedType = type?.toLowerCase();
  if (normalizedType === 'web' || normalizedType === 'websearch') {
    return 'web';
  }
  if (normalizedType === 'datasource') {
    return 'datasource';
  }
  return 'rag';
}

export function isRagCitation(detail?: ChatCitation | null, type?: string) {
  return normalizeCitationType(detail?.type || type) === 'rag';
}

/**
 * F061: which kind is this, for a caller that wants to label it.
 *
 * A KEY, not a translated string. New code should translate this via
 * `useLocalize` at the render site rather than adding to `getCitationSourceLabel`
 * below — that function's literals are part of this file's frozen
 * hardcoded-Chinese count, which may only shrink.
 */
export type CitationSourceKind = 'web' | 'rag' | 'datasource';

export function getCitationSourceKind(type?: string): CitationSourceKind {
  return normalizeCitationType(type);
}

/**
 * The label for a citation kind, in the user's language.
 *
 * Two locales are missing on purpose: `getCitationSourceLabel` below is the
 * legacy hardcoded-Chinese one kept for `Markdown.tsx`'s aria-label, and it
 * cannot learn a third case without growing a frozen count. Everything new
 * routes through here.
 */
export function getCitationSourceLabelI18n(
  kind: CitationSourceKind,
  localize: (key: string) => string,
): string {
  if (kind === 'web') return localize('com_citation_source_web');
  if (kind === 'datasource') return localize('com_citation_source_datasource');
  return localize('com_citation_source_document');
}

/**
 * The legacy two-kind label. Kept exactly as HEAD wrote it: its two literals are
 * part of this file's frozen hardcoded-Chinese count (which may only shrink),
 * and `Markdown.tsx` uses it for an aria-label. Callers that need a datasource
 * label use `getCitationSourceLabelI18n` above.
 */
export function getCitationSourceLabel(type?: string) {
  return normalizeCitationType(type) === 'web' ? '网页' : '文档';
}

export function getCitationItem(detail: ChatCitation | null, itemId?: string) {
  const items = detail?.sourcePayload?.items;
  if (!items?.length) {
    return null;
  }

  return items.find((item) => item.itemId === itemId || item.chunkId === itemId) ?? items[0];
}

export function getCitationDocumentName(detail?: ChatCitation | null) {
  const payload = detail?.sourcePayload;
  const firstItem = payload?.items?.[0];
  const candidates = [
    payload?.documentName,
    payload?.fileName,
    payload?.filename,
    payload?.file_name,
    firstItem?.documentName,
    firstItem?.fileName,
    firstItem?.filename,
    firstItem?.file_name,
    firstItem?.title,
    payload?.title,
    payload?.knowledgeName,
  ];

  const normalized = candidates
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    // Avoid using chat-session titles as document names.
    .filter((item) => !/^(new chat|新对话)$/i.test(item));

  return normalized[0] || '文档预览';
}

export function getCitationDocumentFileType(detail?: ChatCitation | null) {
  const payload = detail?.sourcePayload;
  const fileType = payload?.fileType || getCitationDocumentName(detail).split('.').pop() || '';
  return String(fileType).toLowerCase();
}

export function getCitationDocumentPreviewUrl(detail?: ChatCitation | null) {
  const payload = detail?.sourcePayload;
  return payload?.downloadUrl || '';
}

function getCitationKnowledgeFileId(detail?: ChatCitation | null): number | null {
  const documentId = detail?.sourcePayload?.documentId;
  if (typeof documentId === 'number' && Number.isFinite(documentId)) return documentId;
  if (typeof documentId === 'string' && documentId.trim() !== '' && Number.isFinite(Number(documentId))) {
    return Number(documentId);
  }
  return null;
}

export function getCitationDocumentUrl(detail?: ChatCitation | null) {
  // For knowledge-base citations with a documentId, prefer the freshly signed
  // preview URL fetched via /api/v1/knowledge/file_share (handled by
  // resolveCitationDocumentUrl). Returning '' here forces callers to take the
  // async path so the cached/stale downloadUrl on sourcePayload isn't used.
  if (getCitationKnowledgeFileId(detail) != null) return '';
  return getCitationDocumentPreviewUrl(detail);
}

const inflightFileShareCache: Record<string, Promise<string>> = {};

export async function resolveCitationDocumentUrl(detail?: ChatCitation | null) {
  const fileId = getCitationKnowledgeFileId(detail);
  if (fileId != null) {
    const cacheKey = String(fileId);
    if (!inflightFileShareCache[cacheKey]) {
      inflightFileShareCache[cacheKey] = (async () => {
        try {
          const res: any = await getFilePathApi(cacheKey);
          const data = res?.data ?? res;
          return data?.preview_url || data?.original_url || '';
        } catch {
          return '';
        } finally {
          // Drop after settle so a later open re-fetches a fresh signed URL
          // (signed URLs expire and we don't want to pin a dead one).
          setTimeout(() => { delete inflightFileShareCache[cacheKey]; }, 0);
        }
      })();
    }
    const url = await inflightFileShareCache[cacheKey];
    if (url) return url;
  }
  // Legacy fallback for non-knowledge or older payloads without documentId.
  return getCitationDocumentPreviewUrl(detail);
}

export function toAbsolutePreviewUrl(url?: string | null) {
  if (!url) {
    return '';
  }

  if (/^https?:\/\//i.test(url)) {
    return url;
  }

  if (url.startsWith('/')) {
    return `${window.location.origin}${url}`;
  }

  return `${window.location.origin}${__APP_ENV__.BASE_URL}/${url.replace(/^\/+/, '')}`;
}

function isValidBBox(value: unknown): value is [number, number, number, number] {
  return Array.isArray(value)
    && value.length === 4
    && value.every((item) => typeof item === 'number' && Number.isFinite(item));
}

export function parseCitationBBoxes(rawBBox?: string | null): CitationPdfBBox[] {
  if (!rawBBox) {
    return [];
  }

  try {
    const parsed = JSON.parse(rawBBox);
    const chunkBBoxes = Array.isArray(parsed?.chunk_bboxes) ? parsed.chunk_bboxes : [];
    return chunkBBoxes
      .map((item: any) => ({
        page: Number(item?.page),
        bbox: item?.bbox,
      }))
      .filter((item): item is CitationPdfBBox => Number.isFinite(item.page) && isValidBBox(item.bbox));
  } catch {
    return [];
  }
}

export function getCitationItemBBoxes(detail: ChatCitation | null, itemId?: string) {
  const item = getCitationItem(detail, itemId);
  return parseCitationBBoxes(item?.bbox);
}

export function getLegacyCitationPreview(webContent: any, label?: number): CitationPreview | null {
  if (!label) {
    return null;
  }

  const item = webContent?.[label - 1];
  if (!item) {
    return null;
  }

  return {
    title: item.title || item.url || `引用 ${label}`,
    snippet: item.snippet || item.content || '',
    sourceName: item.source || item.url || '网页',
    sourceMeta: formatCitationWebDate(item.datePublished || item.date || ''),
    link: item.url,
    type: 'web',
  };
}

function extractTaggedContent(value?: string | null, tagName?: string) {
  if (!value || !tagName) {
    return '';
  }

  const pattern = new RegExp(`<${tagName}>([\\s\\S]*?)<\\/${tagName}>`, 'i');
  return value.match(pattern)?.[1]?.trim() || value;
}

function extractRagParagraphContent(value?: string | null) {
  return extractTaggedContent(value, 'paragraph_content');
}

function extractWebSnippetContent(value?: string | null) {
  return value || '';
}

export function buildCitationPreview(detail: ChatCitation | null, data: Partial<CitationDisplayData>): CitationPreview | null {
  if (!detail?.sourcePayload) {
    return null;
  }

  const payload = detail.sourcePayload;
  const item = getCitationItem(detail, data.itemId);
  const type = normalizeCitationType(detail.type || data.type);

  if (type === 'web') {
    return {
      title: item?.title || payload.title || payload.url || `引用 ${data.label ?? ''}`,
      snippet: extractWebSnippetContent(item?.snippet || payload.snippet),
      sourceName: payload.source || payload.url || '网页',
      sourceMeta: formatCitationWebDate(payload.datePublished || ''),
      link: payload.url || payload.sourceUrl,
      type,
    };
  }

  return {
    title: getCitationDocumentName(detail) || `引用 ${data.label ?? ''}`,
    snippet: extractRagParagraphContent(item?.content || item?.snippet || payload.snippet),
    sourceName: payload.knowledgeName || payload.fileType || '政策文件',
    sourceMeta: payload.page ? `第 ${payload.page} 页` : item?.page ? `第 ${item.page} 页` : '',
    link: payload.downloadUrl,
    type,
  };
}

export function buildCitationDocumentPreview(detail: ChatCitation | null, data: Partial<CitationDisplayData>): CitationPreview | null {
  if (!detail?.sourcePayload) {
    return null;
  }

  const payload = detail.sourcePayload;
  const type = normalizeCitationType(detail.type || data.type);

  if (type === 'web') {
    return {
      title: payload.title || payload.url || `引用 ${data.label ?? ''}`,
      snippet: '',
      sourceName: payload.source || payload.url || '网页',
      sourceMeta: formatCitationWebDate(payload.datePublished || ''),
      link: payload.url || payload.sourceUrl,
      type,
    };
  }

  return {
    title: getCitationDocumentName(detail) || `引用 ${data.label ?? ''}`,
    snippet: '',
    sourceName: payload.knowledgeName || payload.fileType || '政策文件',
    sourceMeta: payload.fileType || '',
    link: payload.downloadUrl,
    type,
  };
}

/**
 * A database anchor's content, normalized for the expand panel (F061 AC-31/32/33).
 *
 * Built from the payload the resolve endpoint returns, which is the same
 * `DatasourceCitationPayloadSchema` the turn wrote — so what the user expands is
 * what actually ran, not a re-derivation.
 *
 * `columns` and `rows` are positional: the backend sends the column names once
 * and each row as an array, in the same order. `columns` can be shorter than a
 * row (a driver that reports no names), so the panel addresses cells by index
 * and pads rather than zipping — zipping would silently drop the extra cells.
 */
export type DatasourceAnchor = {
  datasourceId: number | null;
  datasourceName: string;
  tables: string[];
  statement: string;
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  /** True when there is a result table worth a download button. */
  hasRows: boolean;
};

export function getDatasourceAnchor(detail?: ChatCitation | null): DatasourceAnchor | null {
  if (!isDatasourceCitation(detail)) {
    return null;
  }

  // `sourcePayload` already carries an index signature, so no `any` is needed
  // here — and adding one would grow this file's suppression entry, which is
  // count-based and may only shrink.
  const payload = detail?.sourcePayload || {};
  const rawRows = Array.isArray(payload.rows) ? payload.rows : [];
  const rows = rawRows.map((row: unknown) =>
    Array.isArray(row) ? row : row === null || row === undefined ? [] : [row],
  );

  return {
    datasourceId: typeof payload.datasourceId === 'number' ? payload.datasourceId : null,
    datasourceName: String(payload.datasourceName || '').trim(),
    tables: Array.isArray(payload.tables) ? payload.tables.map((t: unknown) => String(t)) : [],
    statement: String(payload.statement || ''),
    columns: Array.isArray(payload.columns) ? payload.columns.map((c: unknown) => String(c)) : [],
    rows,
    truncated: Boolean(payload.truncated),
    hasRows: rows.length > 0,
  };
}

/**
 * The anchor as CSV, for the download action (AC-33).
 *
 * Quoting follows RFC 4180: a field is wrapped when it contains a comma, a
 * quote or a newline, and an embedded quote is doubled. Escaping only the comma
 * is the common shortcut and it corrupts any cell holding a quote character —
 * which, in database data, is the normal case rather than the edge case.
 *
 * A leading `=`, `+`, `-` or `@` is prefixed with a single quote. Spreadsheets
 * execute those as formulas, and this file is written from customer database
 * content — a cell is untrusted input by definition.
 */
export function buildDatasourceCsv(anchor: DatasourceAnchor): string {
  const escapeCell = (value: unknown) => {
    const text = value === null || value === undefined ? '' : String(value);
    const guarded = /^[=+\-@]/.test(text) ? `'${text}` : text;
    return /[",\r\n]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
  };

  // Fall back to positional names so a nameless column still gets a header
  // cell — dropping it would shift every column after it.
  const width = Math.max(
    anchor.columns.length,
    ...anchor.rows.map((row) => row.length),
    0,
  );
  const header = Array.from({ length: width }, (_, index) => anchor.columns[index] ?? `column_${index + 1}`);
  const lines = [header, ...anchor.rows].map((row) =>
    Array.from({ length: width }, (_, index) => escapeCell(row[index])).join(','),
  );
  return lines.join('\r\n');
}

export function createCitationDetailMap(citations?: ChatCitation[] | null) {
  return (citations ?? []).reduce<Record<string, ChatCitation>>((acc, item) => {
    if (item?.citationId) {
      acc[item.citationId] = item;
    }
    return acc;
  }, {});
}

function buildFallbackCitationData(citation: ChatCitation, label: number): CitationDisplayData | null {
  if (!citation?.citationId) {
    return null;
  }

  const item = citation.sourcePayload?.items?.[0];
  const itemId = String(citation.itemId || item?.itemId || item?.chunkId || item?.chunkIndex || '1');
  const parsed = buildCitationDisplayData(`${citation.citationId}:${itemId}`);

  return {
    label,
    ref: parsed?.ref || `${citation.citationId}:${itemId}`,
    type: parsed?.type || citation.type || 'knowledgeSearch',
    groupKey: parsed?.groupKey || citation.citationId,
    chunkId: parsed?.chunkId || itemId,
    citationId: citation.citationId,
    itemId,
  };
}

export function buildCitationReferenceItems({
  content,
  webContent,
  citations,
}: {
  content: string;
  webContent?: any;
  citations?: ChatCitation[] | null;
}): CitationReferenceItem[] {
  const detailMap = createCitationDetailMap(citations);
  const { transformedContent, citationMap } = transformPrivateCitations(content || '');
  const items: CitationReferenceItem[] = [];
  const seen = new Set<string>();

  Object.values(citationMap).forEach((data) => {
    const key = `private:${data.citationId}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    items.push({
      key,
      data,
      detail: detailMap[data.citationId] ?? null,
    });
  });

  for (const match of transformedContent.matchAll(/\[citation:(\d+)\]/g)) {
    const label = Number(match[1]);
    const preview = getLegacyCitationPreview(webContent, label);
    if (!preview) {
      continue;
    }

    const key = `legacy:${label}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    items.push({
      key,
      data: {
        label,
        ref: `citation:${label}`,
        type: 'web',
        groupKey: String(label),
        chunkId: String(label),
        citationId: `citation:${label}`,
        itemId: String(label),
      },
      legacyPreview: preview,
    });
  }

  if (!items.length && citations?.length) {
    citations.forEach((citation, index) => {
      const data = buildFallbackCitationData(citation, index + 1);
      if (!data) {
        return;
      }

      const key = `detail:${data.citationId}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      items.push({
        key,
        data,
        detail: citation,
      });
    });
  }

  return items;
}
