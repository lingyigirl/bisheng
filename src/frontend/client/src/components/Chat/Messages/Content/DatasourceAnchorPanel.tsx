import { useState } from 'react';
import { Outlined } from 'bisheng-icons';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';
import type { ChatCitation } from '~/api/chatApi';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/Collapsible';
import {
  buildDatasourceCsvBlob,
  buildDatasourceExportFilename,
  getDatasourceAnchor,
  type DatasourceAnchor,
} from './citationUtils';

interface Props {
  detail?: ChatCitation | null;
  className?: string;
}

/**
 * F061 T-41: the anchor a database answer carries, and its result table.
 *
 * Collapsed by default (AC-32): the answer text is what the user asked for, and
 * a multi-column result table opened inline would push it off screen. The
 * statement and table list are the "why should I trust this number" details, so
 * they sit one click away rather than in the way.
 *
 * Renders NOTHING when there is no anchor (AC-36). That is the whole contract
 * with the caller: a turn that never queried a database must not gain an empty
 * section, because an empty expander reads as "a database was consulted and
 * returned nothing". `getDatasourceAnchor` returning null is the signal, and
 * this component's early return is what honours it.
 */
export function DatasourceAnchorPanel({ detail, className }: Props) {
  const localize = useLocalize();
  const [open, setOpen] = useState(false);
  const anchor = getDatasourceAnchor(detail);

  // AC-36 — no datasource, no section. Not an empty one.
  if (!anchor) {
    return null;
  }

  const handleDownload = () => {
    // Built here rather than in the util so the filename can carry the
    // datasource's name and the moment of export.
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const url = window.URL.createObjectURL(buildDatasourceCsvBlob(anchor));
    const link = document.createElement('a');
    link.href = url;
    link.download = buildDatasourceExportFilename(anchor, stamp);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  };

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn('rounded-md border border-border-base bg-white', className)}
    >
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex w-full min-w-0 items-center gap-1.5 px-2 py-1.5 text-left text-[12px] leading-5 text-text-2 transition-colors hover:bg-fill-1"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="size-3.5 shrink-0" />
          ) : (
            <ChevronRight className="size-3.5 shrink-0" />
          )}
          <Outlined.Download className="size-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate" title={anchor.datasourceName}>
            {anchor.datasourceName || localize('com_citation_source_datasource')}
            {anchor.tables.length > 0 ? ` · ${anchor.tables.join(', ')}` : ''}
          </span>
          {anchor.hasRows && (
            <span className="shrink-0 text-text-3">
              {localize('com_citation_datasource_rows').replace('{count}', String(anchor.rows.length))}
            </span>
          )}
        </button>
      </CollapsibleTrigger>

      <CollapsibleContent>
        <div className="border-t border-border-base px-2 py-2">
          <DatasourceStatement anchor={anchor} />

          {anchor.hasRows ? (
            <>
              {anchor.truncated && (
                <p className="mb-1 text-[12px] leading-5 text-warning">
                  {localize('com_citation_datasource_truncated')}
                </p>
              )}
              <ResultTable anchor={anchor} />
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={handleDownload}
                  className="inline-flex items-center gap-1 rounded-[4px] px-2 py-1 text-[12px] leading-5 text-blue-500 transition-colors hover:bg-blue-500/[0.07]"
                >
                  <Outlined.Download className="size-3.5" />
                  {localize('com_citation_datasource_download')}
                </button>
              </div>
            </>
          ) : (
            <p className="text-[12px] leading-5 text-text-3">
              {localize('com_citation_datasource_no_rows')}
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function DatasourceStatement({ anchor }: { anchor: DatasourceAnchor }) {
  if (!anchor.statement) {
    return null;
  }
  return (
    <div className="mb-2">
      <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-fill-1 p-1.5 font-mono text-[12px] leading-5 text-text-1">
        {anchor.statement}
      </pre>
    </div>
  );
}

/**
 * The result table.
 *
 * The payload is positional — `columns` once, each row as an array — so cells are
 * addressed by index and a row wider than its header keeps its extra cells.
 * The width is the max of both, matching `buildDatasourceCsv`: rendering the two
 * differently would mean the download disagreed with what was on screen.
 */
function ResultTable({ anchor }: { anchor: DatasourceAnchor }) {
  const localize = useLocalize();
  const width = Math.max(
    anchor.columns.length,
    ...anchor.rows.map((row) => row.length),
    0,
  );

  return (
    <div className="max-h-64 overflow-auto rounded border border-border-base">
      <table className="w-full border-collapse text-[12px] leading-5">
        <thead className="sticky top-0 bg-fill-1">
          <tr>
            {Array.from({ length: width }, (_, index) => (
              <th
                key={index}
                className="whitespace-nowrap border-b border-border-base px-2 py-1 text-left font-normal text-text-2"
              >
                {anchor.columns[index] ?? localize('com_citation_datasource_column').replace('{index}', String(index + 1))}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {anchor.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-border-base last:border-b-0">
              {Array.from({ length: width }, (_, cellIndex) => (
                <td key={cellIndex} className="max-w-[320px] truncate px-2 py-1 text-text-1" title={formatCell(row[cellIndex])}>
                  {formatCell(row[cellIndex])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** NULL renders as an em dash: an empty cell is indistinguishable from "" . */
function formatCell(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  return String(value);
}
