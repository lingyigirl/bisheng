/**
 * F065 T-06 / T-07 — the knowledge-space file browser.
 *
 * Two panes: the spaces stay in a left rail (grouped 部门 / 我创建的 / 我加入的,
 * first one chosen on open), while the right pane browses the current location
 * with a back arrow, the path trail and a keyword search. Space rows stay put,
 * so switching spaces never costs a trip back through a flat list.
 *
 * Pick-only: it browses spaces → folders → files and hands back what was
 * selected. Turning those picks into form field values (and reporting the ones
 * that fail) is `resolvePickedValues`, kept outside so this component needs no
 * knowledge of the form field it serves.
 */

import { Button } from '@bisheng/ui';
import { ChevronLeft, FileText, Folder, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
// Narrow imports on purpose: the `~/components/ui` barrel pulls the whole
// component index (and, through it, ESM-only deps) into anything that renders or
// tests a single dialog — see F065 T-06 / 坑 16.
import { Checkbox } from '~/components/ui/Checkbox';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '~/components/ui/Dialog';
import { ExpandableSearchField } from '~/components/ui/ExpandableSearchField';
import useDebounce from '~/hooks/Input/useDebounce';
import { useCategorizedKnowledgeSpaces } from '~/hooks/useCategorizedKnowledgeSpaces';
import useLocalize from '~/hooks/useLocalize';
import { cn } from '~/utils';
import type { PickedKnowledgeFile } from './resolvePickedValues';
import { selectableFileIds, triState } from './selectAll';
import { matchesSuffix } from './suffixMatch';
import { toBrowseChildren, useSpaceChildren, type BrowseChild } from './useKnowledgeBrowse';

interface KnowledgeFilePickerProps {
  open: boolean;
  /** Single-select when false: picking a file replaces the current choice (AC-03). */
  multiple: boolean;
  onClose: () => void;
  onConfirm: (picked: PickedKnowledgeFile[]) => void;
  /** The form item's own file-type list; anything else is not pickable (AC-29). */
  suffixes?: string[];
}

/** One step of the folder trail below the space root. */
interface PathCrumb {
  id: string;
  name: string;
}

const SEARCH_DEBOUNCE_MS = 300;

/** Selection is keyed by space + file, so the same id in two spaces stays distinct. */
function isSelected(
  selected: Map<string, PickedKnowledgeFile>,
  spaceId: string | null,
  child: BrowseChild,
): boolean {
  return !!spaceId && selected.has(`${spaceId}:${child.id}`);
}

export function KnowledgeFilePicker({
  open,
  multiple,
  onClose,
  onConfirm,
  suffixes,
}: KnowledgeFilePickerProps) {
  const localize = useLocalize();
  const { groups, isFetching: spacesFetching } = useCategorizedKnowledgeSpaces({ enabled: open });
  const spaces = useMemo(() => groups.flatMap((group) => group.spaces), [groups]);

  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [path, setPath] = useState<PathCrumb[]>([]);
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<Map<string, PickedKnowledgeFile>>(new Map());
  const debouncedKeyword = useDebounce(keyword, SEARCH_DEBOUNCE_MS).trim();

  const space = spaces.find((item) => item.id === spaceId);
  const parentId = path.length ? path[path.length - 1].id : null;

  // Every open starts clean. Carrying the previous keyword / trail / selection
  // over would make the next open look like the old picks are still in play.
  useEffect(() => {
    if (!open) return;
    setKeyword('');
    setPath([]);
    setSelected(new Map());
  }, [open]);

  // Land on a space the moment the list arrives (AC-01). Keeping the previous
  // choice is fine while it is still visible; a space that disappeared (left,
  // renamed away) must not leave the right pane browsing nothing.
  useEffect(() => {
    if (!open || !spaces.length) return;
    if (spaceId && spaces.some((item) => item.id === spaceId)) return;
    setSpaceId(spaces[0].id);
  }, [open, spaceId, spaces]);

  const {
    data,
    isFetching: childrenFetching,
    isError,
    hasNextPage,
    fetchNextPage,
  } = useSpaceChildren(space, parentId, debouncedKeyword, open);

  const children = toBrowseChildren((data?.pages ?? []).flatMap((page) => page.data));

  // What "select all" may pick HERE (AC-30) and what the tri-state row shows.
  const pickableIds = useMemo(() => selectableFileIds(children, suffixes), [children, suffixes]);
  const pickedHereCount = pickableIds.filter((id) => selected.has(`${spaceId}:${id}`)).length;

  const selectSpace = (next: { id: string }) => {
    setSpaceId(next.id);
    setPath([]);
    setKeyword('');
  };

  const openFolder = (folder: BrowseChild) => {
    setPath((prev) => [...prev, { id: folder.id, name: folder.name }]);
    // A keyword belongs to the location it was typed in; descending starts over.
    setKeyword('');
  };

  const toggleFile = (file: BrowseChild) => {
    if (!spaceId) return;
    const key = `${spaceId}:${file.id}`;
    const picked: PickedKnowledgeFile = { spaceId, fileId: file.id, name: file.name };
    setSelected((prev) => {
      if (prev.has(key)) {
        const next = new Map(prev);
        next.delete(key);
        return next;
      }
      // Single-select keeps only the newest pick (AC-03).
      return multiple ? new Map(prev).set(key, picked) : new Map([[key, picked]]);
    });
  };

  /**
   * Mass pick / un-pick of the current location (AC-30 / AC-31).
   *
   * Merges into the existing selection rather than replacing it: picks made in
   * other folders are none of this action's business — un-picking here removes
   * only the files on screen. "Clear all" is the separate footer action.
   */
  const toggleAllHere = (pick: boolean) => {
    if (!multiple || !spaceId) return;
    const rowsById = new Map(children.map((child) => [child.id, child]));
    setSelected((prev) => {
      const next = new Map(prev);
      for (const id of pickableIds) {
        const key = `${spaceId}:${id}`;
        if (!pick) {
          next.delete(key);
          continue;
        }
        const child = rowsById.get(id);
        if (child) next.set(key, { spaceId, fileId: id, name: child.name });
      }
      return next;
    });
  };

  const handleConfirm = () => {
    if (selected.size === 0) return;
    onConfirm([...selected.values()]);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        data-testid="knowledge-file-picker"
        className="flex max-w-4xl flex-col"
        // The search field would otherwise steal focus on open and pop the
        // window's soft keyboard before the user has seen the list.
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <DialogHeader className="text-left">
          <DialogTitle>{localize('com_knowledge.pick_files_title')}</DialogTitle>
        </DialogHeader>

        <div className="flex h-[420px] overflow-hidden rounded-lg border border-border-base">
          <div className="flex w-56 shrink-0 flex-col overflow-auto border-r border-border-base p-2">
            {groups.length === 0 ? (
              <div className="flex h-full items-center justify-center px-3 text-center text-caption text-text-3">
                {localize(spacesFetching ? 'com_knowledge.loading' : 'com_knowledge.no_data')}
              </div>
            ) : (
              groups.map((group) => (
                <div key={group.key} className="flex flex-col">
                  <div className="px-2 py-1 text-[11px] text-text-3">{group.label}</div>
                  {group.spaces.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      data-testid={`picker-space-${item.id}`}
                      aria-current={item.id === spaceId}
                      title={item.name}
                      className={cn(
                        'flex items-center gap-2 truncate rounded-md px-2 py-1.5 text-left text-body-sm',
                        item.id === spaceId ? 'bg-fill-2 text-text-1' : 'text-text-2 hover:bg-fill-2',
                      )}
                      onClick={() => selectSpace(item)}
                    >
                      <Folder size={14} className="shrink-0" />
                      <span className="truncate">{item.name}</span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-border-base p-3">
              {path.length > 0 && (
                <Button
                  variant="ghost"
                  size="icon"
                  data-testid="picker-back"
                  aria-label={localize('com_knowledge.knowledge_space')}
                  onClick={() => setPath((prev) => prev.slice(0, -1))}
                >
                  <ChevronLeft size={16} />
                </Button>
              )}
              <div className="flex min-w-0 items-center truncate text-caption text-text-3">
                <span className="truncate">{space?.name ?? ''}</span>
                {path.map((folder) => (
                  <span key={folder.id} className="truncate">
                    {` / ${folder.name}`}
                  </span>
                ))}
              </div>
              <div className="ml-auto w-56 shrink-0">
                <ExpandableSearchField
                  alwaysExpanded
                  showClearButton
                  data-testid="picker-search"
                  value={keyword}
                  onChange={setKeyword}
                  placeholder={localize('com_knowledge.pick_search_placeholder')}
                  expandedWidthClassName="w-full"
                  containerClassName="!rounded-md"
                />
              </div>
            </div>

            {/* Bulk pick of this location (AC-30). Absent in single-select,
                where "all" has no meaning (AC-32). */}
            {multiple && (
              <div className="flex shrink-0 items-center gap-2 border-b border-border-base px-3 py-2">
                <Checkbox
                  checked={triState(pickedHereCount, pickableIds.length)}
                  disabled={pickableIds.length === 0}
                  data-testid="picker-select-all"
                  aria-label={localize('com_knowledge.pick_select_all')}
                  onCheckedChange={(next) => toggleAllHere(next === true)}
                />
                <button
                  type="button"
                  disabled={pickableIds.length === 0}
                  className="text-body-sm text-text-2 disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={() => toggleAllHere(triState(pickedHereCount, pickableIds.length) !== true)}
                >
                  {localize('com_knowledge.pick_select_all')}
                </button>
                <span className="ml-auto shrink-0 text-[11px] text-text-3">
                  {localize('com_knowledge.pick_select_all_count', {
                    0: pickedHereCount,
                    1: pickableIds.length,
                  })}
                </span>
              </div>
            )}

            <div className="flex-1 overflow-auto p-1">
              {childrenFetching && children.length === 0 && (
                <div className="flex h-full items-center justify-center gap-2 text-caption text-text-3">
                  <Loader2 size={14} className="animate-spin" />
                  {localize('com_knowledge.loading')}
                </div>
              )}
              {!childrenFetching && isError && children.length === 0 && (
                <div className="flex h-full items-center justify-center px-3 text-center text-caption text-text-3">
                  {localize('com_knowledge.pick_load_failed')}
                </div>
              )}
              {!childrenFetching && !isError && children.length === 0 && (
                <div className="flex h-full items-center justify-center px-3 text-center text-caption text-text-3">
                  {localize(
                    debouncedKeyword ? 'com_knowledge.no_matched_file' : 'com_knowledge.no_file_here',
                  )}
                </div>
              )}

              {children.map((child) => {
                if (child.isFolder) {
                  return (
                    <button
                      key={child.id}
                      type="button"
                      data-testid={`picker-folder-${child.id}`}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-body-sm text-text-1 hover:bg-fill-2"
                      onClick={() => openFolder(child)}
                    >
                      <Folder size={16} className="shrink-0 text-text-3" />
                      <span className="truncate" title={child.name}>
                        {child.name}
                      </span>
                    </button>
                  );
                }

                const pickable = matchesSuffix(child.name, suffixes);
                const picked = isSelected(selected, spaceId, child);
                return (
                  // A div rather than a button: the row contains the Radix
                  // checkbox, which is itself a button, and buttons may not nest.
                  <div
                    key={child.id}
                    role="button"
                    tabIndex={pickable ? 0 : -1}
                    data-testid={`picker-file-${child.id}`}
                    aria-pressed={picked}
                    aria-disabled={!pickable}
                    onClick={() => pickable && toggleFile(child)}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' && event.key !== ' ') return;
                      event.preventDefault();
                      if (pickable) toggleFile(child);
                    }}
                    className={cn(
                      'flex items-center gap-2 rounded-md px-2 py-1.5 text-body-sm',
                      pickable ? 'cursor-pointer hover:bg-fill-2' : 'cursor-not-allowed opacity-60',
                      picked ? 'bg-blue-500/[0.07] text-blue-600' : 'text-text-1',
                    )}
                  >
                    <Checkbox
                      checked={picked}
                      disabled={!pickable}
                      tabIndex={-1}
                      className="pointer-events-none"
                    />
                    <FileText size={16} className="shrink-0 text-text-3" />
                    <span className="truncate" title={child.name}>
                      {child.name}
                    </span>
                    {!pickable && (
                      <span className="ml-auto shrink-0 text-[11px] text-text-3">
                        {localize('com_knowledge.pick_reason_suffix')}
                      </span>
                    )}
                  </div>
                );
              })}

              {hasNextPage && (
                <div className="flex justify-center py-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="picker-load-more"
                    disabled={childrenFetching}
                    onClick={() => fetchNextPage()}
                  >
                    {localize('com_knowledge.pick_load_more')}
                  </Button>
                </div>
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <span className="text-caption text-text-3" data-testid="picker-selected-count">
              {localize('com_knowledge.pick_selected_count', { 0: selected.size })}
            </span>
            {/* Cross-folder clear (AC-31); single-select has no bulk entries (AC-32). */}
            {multiple && (
              <Button
                size="sm"
                variant="ghost"
                data-testid="picker-clear-all"
                disabled={selected.size === 0}
                onClick={() => setSelected(new Map())}
              >
                {localize('com_knowledge.pick_clear_all')}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" data-testid="picker-cancel" onClick={onClose}>
              {localize('com_knowledge.cancel')}
            </Button>
            <Button size="sm" data-testid="picker-confirm" disabled={selected.size === 0} onClick={handleConfirm}>
              {localize('com_knowledge.confirm')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
