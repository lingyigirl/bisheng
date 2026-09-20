import { Database } from 'lucide-react';
import { useLocalize } from '~/hooks';

interface Props {
  /**
   * The bound datasources' names, already resolved.
   *
   * A plain prop rather than a store read: the state lives in Recoil, which is
   * frozen (ledger #5) — a new `recoil` import anywhere is rejected by lint, and
   * the parent that already reads the chat state derives this for us.
   */
  datasources: string[];
}

/**
 * F061 T-40: which sources this app can draw on.
 *
 * READ-ONLY, and that is a deliberate narrowing rather than an omission. The
 * spec's original wording had the client *choose* sources per turn, but the
 * datasource tools are injected once from the assistant's admin-configured
 * bindings (`AssistantAgent.init_tools`), and the chat path that does support a
 * per-turn selection — the workstation SSE one — has no datasource tools at all.
 * Its datasource half is registered as a known gap in the spec (AC-24), to be
 * built as its own feature. So there is nothing here for a picker to select.
 *
 * Showing the user what an answer can come from is what makes AC-58's 来源可辨
 * possible without waiting for that feature: the anchor rendering (T-41) says
 * where a given answer came from, and this says what was available to it.
 *
 * Renders nothing when the app binds no datasource. Every app in the install
 * base is that case today, and an empty "数据源:" row would be noise on all of
 * them.
 */
export function AppSourceSummary({ datasources }: Props) {
  const localize = useLocalize();

  if (!datasources.length) {
    return null;
  }

  const names = datasources.join(', ');

  return (
    <div className="mx-auto w-full max-w-[800px] px-4">
      <div className="flex min-w-0 items-center gap-1.5 pb-1 text-caption text-text-3">
        <Database className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="shrink-0">{localize('com_citation_source_datasource')}</span>
        <span className="min-w-0 truncate" title={names}>
          {names}
        </span>
      </div>
    </div>
  );
}
