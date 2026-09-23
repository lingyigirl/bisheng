import {
  Glasses,
  PaperclipIcon,
} from "lucide-react";
import { Outlined } from "bisheng-icons";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "~/components/ui";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "~/components/ui/Tooltip2";
import { BsConfig } from "~/types/chat";
import { useCategorizedKnowledgeSpaces, useLocalize, useMediaQuery } from "~/hooks";
import { useToastContext } from "~/Providers";
import { cn } from "~/utils";
import { hasKnowledgeSpaces, resolveOrgScope, withOrgScope } from "~/utils/knowledgeScope";
import { KnowledgeListPanel, type KnowledgeListItem } from "./KnowledgeListPanel";
import type { KnowledgeItem } from "./knowledgeTypes";

export type { KnowledgeItem, KnowledgeType } from "./knowledgeTypes";

// --- Hooks ---
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
}

/** Cap for the mobile popup / drill panels (further clamped to whatever space
 *  the viewport actually offers — see mobileDrillMaxH). */
const MAX_SUB_HEIGHT = 256;
/** Cap for the desktop 知识空间 panel. Its own value: the skill / org panels sit
 *  at 440, this one stays shorter so it still opens downward more often. */
const KNOWLEDGE_PANEL_MAX_H = 320;
const BOTTOM_GAP = 8;
/** Distance from the chat input box's outer edge to its toolbar: the box's
 *  `p-3` (12px) plus its 1px border (AiChatInput / TaskModeInput shell). */
const INPUT_INNER_INSET = 13;
/** 移动端：碰撞检测余量；底部勿过大，否则 flip/shift 会把整块菜单顶到视口上方导致裁切 */
const MOBILE_MENU_COLLISION = {
  top: 56,
  bottom: 28,
  left: 12,
  right: 12,
} as const;

// --- main ---
export const ChatKnowledge = ({
  variant = 'plus',
  config,
  disabled,
  value = [],
  onChange,
  showFileUpload = false,
  fileUploadDisabled = false,
  onFileUploadClick,
  showTaskModeEntry = false,
  onEnterTaskMode,
  renderSkillSubmenu,
  taskModeActive = false,
  skillSelected = false,
  compact = false,
}: {
  /** Controls the trigger button and which menu sections render:
   *  - 'plus'      → "+" trigger; file-upload + task-mode (+ optional add-skill) sections.
   *  - 'knowledge' → pill trigger; knowledge-space + org-knowledge submenus only. */
  variant?: 'plus' | 'knowledge';
  config?: BsConfig;
  disabled: boolean;
  value: KnowledgeItem[];
  onChange: (val: KnowledgeItem[]) => void;
  /** Render a "上传文件" entry at the top of the menu (v2.5 plus menu). */
  showFileUpload?: boolean;
  fileUploadDisabled?: boolean;
  onFileUploadClick?: () => void;
  /** F035 (PRD §4.1.3): render the "任务模式" entry as a separate group at the
   *  bottom of the "+" menu. Daily chat → navigates to /linsight; routing is
   *  delegated to the caller so this component stays route-free. */
  showTaskModeEntry?: boolean;
  onEnterTaskMode?: () => void;
  /** F035: "添加 Skill" hover submenu (desktop) / drill panel (mobile); selecting a skill enters task mode. */
  renderSkillSubmenu?: (close: () => void) => ReactNode;
  /** When already in task mode, show the entry checked (toggle indicator). */
  taskModeActive?: boolean;
  /** F035: tint the "添加技能" icon brand-blue once at least one skill is picked. */
  skillSelected?: boolean;
  /** Toolbar out of room (see useContainerCompact): collapse label to icon. */
  compact?: boolean;
}) => {
  const localize = useLocalize();
  const MAX_KB_PER_TYPE = 50;
  const { showToast } = useToastContext();

  // checked data
  const selectedKnowledgeSpaces = useMemo(
    () => value.filter((item) => item.type === 'space'),
    [value]
  );

  // F063 — the organization-KB tier (「全部」/「不使用」). Resolved from the state
  // (an explicit pick on the marker entry, else the AC-02 fallback) so the tier
  // is right even when this entry has never been opened this session.
  const orgScope = resolveOrgScope(value, hasKnowledgeSpaces(value));

  // --- Knowledge space data (load all groups at once, no pagination) ---
  const [spaceKeyword, setSpaceKeyword] = useState("");
  const debouncedSpaceKeyword = useDebounce(spaceKeyword, 300);

  // Spaces are only shown inside the open picker, so load them lazily on first
  // open instead of eagerly on mount. The eager mount-fetch fired
  // knowledge/space/{mine,joined} every time the input box re-mounted (e.g. the
  // send-triggered welcome→messages layout flip), causing duplicate requests on send.
  const [rootOpen, setRootOpen] = useState(false);

  // Grouped as 部门知识空间 / 我创建的 / 我加入的, matching the 知识空间 page.
  // Empty groups (including "no search hit in this group") are dropped by the hook.
  const { groups: spaceGroups, isFetching: spaceFetching } = useCategorizedKnowledgeSpaces({
    enabled: rootOpen,
    keyword: debouncedSpaceKeyword,
  });
  const spaceListGroups = useMemo(
    () => spaceGroups.map((group) => ({ key: group.key, label: group.label, items: group.spaces })),
    [spaceGroups],
  );

  // checked data — compare with String() on both sides (API returns numeric
  // ids, but the atom type + default-seeding use strings).
  const handleToggle = (item: KnowledgeListItem) => {
    const itemKey = String(item.id);
    const exists = value.some((i) => String(i.id) === itemKey && i.type === 'space');

    if (exists) {
      const nextValue = value.filter((i) => !(String(i.id) === itemKey && i.type === 'space'));
      onChange(nextValue);
    } else {
      const currentTypeCount = value.filter(i => i.type === 'space').length;

      if (currentTypeCount >= MAX_KB_PER_TYPE) {
        showToast({
          message: localize('com_chat_knowledge_toast_space_limit'),
          status: 'error',
        });
        return;
      }

      // Normalise id to string on insert to keep the atom invariant stable.
      const newItem: KnowledgeItem = { id: itemKey, name: item.name ?? '', type: 'space' };
      onChange([newItem, ...value]);
    }
  };

  // Dead since the pre-F063 toolbar rework (declared, never read). Left in
  // place on purpose: stale project code is annotated, not deleted.
  const hasAnySelection = value.length > 0;
  const orgEnabled = !!config?.knowledgeBase?.enabled;

  // 仅 <=576 走移动端下钻面板；577~768 保持桌面级联交互（右侧展开）
  const isMobile = useMediaQuery('(max-width: 576px)');
  const [mobilePanel, setMobilePanel] = useState<'root' | 'skill'>('root');
  const menuContentRef = useRef<HTMLDivElement>(null);

  const handleRootOpenChange = useCallback((open: boolean) => {
    setRootOpen(open);
    setMobilePanel('root');
  }, []);

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  /** 第二层：高度取触发器上下可用空间的较小值，避免翻转到上方后仍按「很高」排版导致顶出屏幕 */
  const [mobileDrillMaxH, setMobileDrillMaxH] = useState<number | undefined>(undefined);
  const [mobileMenuSide, setMobileMenuSide] = useState<'top' | 'bottom'>('bottom');

  // A "tall list panel" needs the adaptive height cap on mobile: the knowledge
  // pill shows its spaces list directly, and the "+" menu grows tall once it is
  // drilled into the skill list. The "+" root (short action items) is not.
  const mobileTallPanel = variant === 'knowledge' || mobilePanel !== 'root';

  useLayoutEffect(() => {
    if (!isMobile || !rootOpen || !mobileTallPanel) {
      setMobileDrillMaxH(undefined);
      setMobileMenuSide('bottom');
      return;
    }
    const run = () => {
      const el = triggerRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const padT = MOBILE_MENU_COLLISION.top;
      const padB = MOBILE_MENU_COLLISION.bottom;
      const above = r.top - padT;
      const below = window.innerHeight - r.bottom - padB;
      // Mobile adaptive strategy:
      // prefer opening downward when there is enough space;
      // otherwise open upward to avoid being clipped by the chat input area.
      const preferBottom = below >= 240 || below >= above;
      setMobileMenuSide(preferBottom ? 'bottom' : 'top');
      // Fixed cap, but
      // fall back to whatever space is actually available on the chosen side if
      // 256 wouldn't fit — keeps the popup from being clipped against the
      // viewport edge on smaller phones.
      const raw = (preferBottom ? below : above) - 8;
      const capped = Math.min(MAX_SUB_HEIGHT, Math.max(80, Math.floor(raw)));
      setMobileDrillMaxH(capped);
    };
    run();
    const ro = new ResizeObserver(run);
    if (triggerRef.current) ro.observe(triggerRef.current);
    window.addEventListener('resize', run);
    window.addEventListener('scroll', run, true);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', run);
      window.removeEventListener('scroll', run, true);
    };
  }, [isMobile, rootOpen, mobileTallPanel]);

  return (
    <DropdownMenu open={rootOpen} onOpenChange={handleRootOpenChange}>
      <TooltipProvider delayDuration={50}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild disabled={disabled}>
              {variant === 'knowledge' ? (
                <button
                  ref={triggerRef}
                  type="button"
                  className={cn(
                    // `group` lets the chevron pick up the Radix-emitted
                    // `data-state` to mirror the Tools-select rotation.
                    "group flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[14px] font-normal text-text-2 outline-none transition-colors hover:bg-fill-1",
                    disabled && "opacity-50 cursor-not-allowed hover:bg-transparent"
                  )}
                  aria-label={localize('com_ui_knowledge_space')}
                >
                  <div className="relative shrink-0">
                    {/* Icon is neutral by default (matches the + button) and
                        turns brand-blue once a space is selected. */}
                    <span
                      aria-hidden
                      className={cn(
                        "block size-4",
                        selectedKnowledgeSpaces.length > 0 ? "bg-blue-500" : "bg-[#4E5969]"
                      )}
                      style={{
                        WebkitMaskImage: `url(${__APP_ENV__.BASE_URL || ''}/assets/channel/book-one.svg)`,
                        maskImage: `url(${__APP_ENV__.BASE_URL || ''}/assets/channel/book-one.svg)`,
                        WebkitMaskRepeat: 'no-repeat', maskRepeat: 'no-repeat',
                        WebkitMaskPosition: 'center', maskPosition: 'center',
                        WebkitMaskSize: 'contain', maskSize: 'contain',
                      }}
                    />
                  </div>
                  {/* Compact: collapse to icon + chevron only to save
                      horizontal space in the input toolbar. */}
                  {!compact && <span>{localize('com_ui_knowledge_space')}</span>}
                  <Outlined.Down size={16} className={cn("text-text-3 transition-transform duration-200", rootOpen && "rotate-180")} />
                </button>
              ) : (
                <button
                  ref={triggerRef}
                  type="button"
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-lg text-text-2 cursor-pointer hover:bg-fill-1 transition-colors outline-none",
                    disabled && "opacity-50 cursor-not-allowed"
                  )}
                  aria-label={localize('com_knowledge_add_file')}
                >
                  <Outlined.Plus size={18} />
                </button>
              )}
            </DropdownMenuTrigger>
          </TooltipTrigger>
          {/* The knowledge pill already shows its label inline, so it needs no
              tooltip; only the icon-only "+" trigger gets one. */}
          {variant !== 'knowledge' && (
            <TooltipContent side="bottom" sideOffset={6}>
              {localize('com_knowledge_add_file')}
            </TooltipContent>
          )}
        </Tooltip>
      </TooltipProvider>

      <DropdownMenuContent
        ref={menuContentRef}
        align="start"
        // The "+" panel lines up with the INPUT BOX's left edge, not its own
        // trigger: the trigger sits INPUT_INNER_INSET in from that edge, so pull
        // the panel back by the same amount. The knowledge pill keeps trigger
        // alignment — it sits mid-toolbar, where the input edge means nothing.
        alignOffset={variant === 'plus' ? -INPUT_INNER_INSET : undefined}
        side={isMobile ? mobileMenuSide : 'bottom'}
        collisionPadding={isMobile ? MOBILE_MENU_COLLISION : BOTTOM_GAP}
        sticky={isMobile ? 'partial' : undefined}
        onCloseAutoFocus={(e) => e.preventDefault()}
        className={cn(
          'flex flex-col gap-1 rounded-2xl border-0 shadow-[0_2px_16px_-2px_rgba(0,23,66,0.10)]',
          // variant-aware width/padding: the pill (knowledge) shows a list
          // directly, so it needs the wider list layout; the "+" menu stays
          // compact for its short action items. Bottom padding is 0 on the list
          // variant — the scroll list carries its own so rows can reach the edge.
          variant === 'knowledge'
            ? 'w-[240px] overflow-hidden pt-3 px-3 pb-0'
            : 'w-[160px] p-3',
          // Mobile width override only applies to the knowledge variant — the
          // "+" menu shows short action items and matches the desktop 160px
          // width on phones too. (knowledge needs more room for search + list)
          // Mobile: any "tall" panel (knowledge, or the "+" menu drilled into
          // skill / org lists) needs the wider width; 160px is fine only for
          // the compact root of the "+" menu (short action items).
          isMobile && mobileTallPanel && 'touch-mobile:w-[min(calc(100vw-24px),320px)]',
          // Any mobile list panel (knowledge popup, or the "+" menu drilled into
          // org / skill): replace `p-3` with `pt-3 px-3 pb-0` so the scroll list's
          // own bottom padding is the only gap under the last row.
          isMobile && mobileTallPanel && 'touch-mobile:pt-3 touch-mobile:px-3 touch-mobile:pb-0',
          isMobile &&
          mobileTallPanel &&
          'touch-mobile:min-h-0 touch-mobile:overflow-hidden',
        )}
        style={
          isMobile && mobileTallPanel && mobileDrillMaxH !== undefined
            ? { maxHeight: mobileDrillMaxH }
            : // Desktop knowledge pill: cap height so the space list scrolls
              // internally instead of growing past the viewport.
              !isMobile && variant === 'knowledge'
              ? { maxHeight: KNOWLEDGE_PANEL_MAX_H }
              : undefined
        }
      >
        {variant === 'plus' && showFileUpload && ((!isMobile) || (isMobile && mobilePanel === 'root')) && (
          <DropdownMenuItem
            disabled={fileUploadDisabled}
            onSelect={(e) => {
              e.preventDefault();
              if (fileUploadDisabled) return;
              onFileUploadClick?.();
            }}
            className="flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2 outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40"
          >
            <Outlined.Attachment size={16} className="text-text-2" />
            <span className="text-[14px] font-normal text-slate-700">{localize('com_ui_upload_files')}</span>
          </DropdownMenuItem>
        )}

        {/* Knowledge pill: show the SPACES list directly — no drill, no sub.
            Same layout on both surfaces; only the outer width / position adapt
            to the smaller screen, plus the mobile-only heading below. */}
        {variant === 'knowledge' && (
          <div className="flex min-h-0 w-full flex-1 flex-col">
            {/* Mobile keeps the heading: the narrow toolbar can collapse the pill
                trigger to icon-only (`compact`), leaving this as the only place
                the name shows. On desktop the trigger reads "知识空间" beside it. */}
            {isMobile && (
              <p className="mb-1 shrink-0 px-2 py-[5px] text-[14px] font-medium leading-[22px] text-[#1A1A1A]">
                {localize('com_ui_knowledge_space')}
              </p>
            )}
            <KnowledgeListPanel
              placeholder={localize('com_chat_knowledge_placeholder_search_space')}
              keyword={spaceKeyword}
              setKeyword={setSpaceKeyword}
              groups={spaceListGroups}
              selectedItems={selectedKnowledgeSpaces}
              onToggle={handleToggle}
              isFetching={spaceFetching}
              hasMore={false}
              onLoadMore={() => { }}
              emptyText={localize('com_chat_knowledge_empty_no_spaces')}
            />
          </div>
        )}

        {/* Org knowledge tier (F063) — 「全部 / 不使用」 and nothing else. The
            per-KB picker was removed with the listing (AC-13/AC-14): "search all
            of them" is now the default, and "narrow it down to a few" is what
            knowledge spaces are for. Same rows on desktop and mobile — one code
            path, so every entry that mounts this input behaves alike (AC-24).
            Gated by the KB feature flag (AC-18). */}
        {variant === 'plus' &&
          ((!isMobile) || (isMobile && mobilePanel === 'root')) &&
          config?.knowledgeBase?.enabled !== false && (
          <>
            <div className="my-1 h-px bg-slate-100" />
            <p className="shrink-0 px-2 py-[5px] text-caption text-text-3">
              {localize('com_tools_org_knowledge')}
            </p>
            {/* `data-org-scope` / `data-selected` mirror the state the tier was
                resolved to, so the panel is inspectable without reading styles. */}
            <DropdownMenuItem
              data-org-scope="all"
              data-selected={orgScope === 'all' ? 'true' : 'false'}
              onSelect={() => onChange(withOrgScope(value, 'all'))}
              className="flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2 outline-none"
            >
              <Outlined.Books size={16} className={orgScope === 'all' ? 'text-blue-500' : 'text-text-2'} />
              <span className={cn('flex-1 text-[14px] font-normal', orgScope === 'all' ? 'text-blue-500' : 'text-slate-700')}>
                {localize('com_chat_knowledge_scope_all')}
              </span>
              {orgScope === 'all' && <Outlined.Check size={14} className="text-blue-500" />}
            </DropdownMenuItem>
            <DropdownMenuItem
              data-org-scope="none"
              data-selected={orgScope === 'none' ? 'true' : 'false'}
              onSelect={() => onChange(withOrgScope(value, 'none'))}
              className="flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2 outline-none"
            >
              <Outlined.Books size={16} className={orgScope === 'none' ? 'text-blue-500' : 'text-text-2'} />
              <span className={cn('flex-1 text-[14px] font-normal', orgScope === 'none' ? 'text-blue-500' : 'text-slate-700')}>
                {localize('com_chat_knowledge_scope_none')}
              </span>
              {orgScope === 'none' && <Outlined.Check size={14} className="text-blue-500" />}
            </DropdownMenuItem>
          </>
        )}

        {/* F035 (PRD §4.1.3) — 任务模式组：以分隔线与上方通用上下文组隔开。
            桌面端与移动端 root 面板都展示；进入任务模式跳转 /linsight。 */}
        {variant === 'plus' && showTaskModeEntry && ((!isMobile) || (isMobile && mobilePanel === 'root')) && (
          <>
            <div className="my-1 h-px bg-slate-100" />
            <DropdownMenuItem
              onSelect={() => {
                // Close the menu before navigating: keeping it open while the
                // trigger unmounts on route change leaves the popover anchorless
                // and it jumps to the top-left corner.
                setRootOpen(false);
                onEnterTaskMode?.();
              }}
              className="flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2 outline-none"
            >
              <Outlined.ListSuccess size={16} className={taskModeActive ? 'text-blue-500' : 'text-text-2'} />
              <span className={cn('flex-1 text-[14px] font-normal', taskModeActive ? 'text-blue-500' : 'text-slate-700')}>
                {localize('com_linsight_task_mode')}
              </span>
              {taskModeActive && <Outlined.Check size={14} className="text-blue-500" />}
            </DropdownMenuItem>
            {/* 添加 Skill — 桌面：悬停展开技能选择器；移动 root：下钻进技能面板。
                选中技能即进入任务模式（由 renderSkillSubmenu 内部导航），故传入
                close 让选择器先关掉「+」菜单，避免 popover 跳位。 */}
            {renderSkillSubmenu && (
              !isMobile ? (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger
                    className={cn(
                      'flex h-8 cursor-pointer items-center justify-between rounded-lg px-2 outline-none',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Outlined.Newspaper size={16} className={skillSelected ? 'text-blue-500' : 'text-text-2'} />
                      <span className="text-[14px] font-normal text-slate-700">
                        {localize('com_linsight_add_skill')}
                      </span>
                    </div>
                  </DropdownMenuSubTrigger>
                  {/* Layout mirrors the knowledge panel shell (variant === 'knowledge' above).
                      `align="center"` centers the panel vertically on the trigger row
                      instead of aligning their top edges. */}
                  <DropdownMenuSubContent
                    align="center"
                    collisionPadding={BOTTOM_GAP}
                    className="ml-2 flex max-h-[440px] w-[280px] flex-col gap-0 overflow-hidden rounded-2xl border-0 bg-white px-3 pb-0 pt-3 shadow-[0_2px_16px_-2px_rgba(0,23,66,0.10)]"
                  >
                    {renderSkillSubmenu(() => setRootOpen(false))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              ) : (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault();
                    setMobilePanel('skill');
                  }}
                  className="flex h-8 cursor-pointer items-center justify-between gap-2 rounded-lg px-2 outline-none"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <Outlined.Newspaper size={16} className={skillSelected ? 'text-blue-500' : 'text-text-2'} />
                    <span className="truncate text-[14px] font-normal text-slate-700">
                      {localize('com_linsight_add_skill')}
                    </span>
                  </div>
                  <Outlined.Right className="size-4 shrink-0 text-slate-400" />
                </DropdownMenuItem>
              )
            )}
          </>
        )}

        {/* 添加 Skill — 移动端下钻面板 */}
        {isMobile && mobilePanel === 'skill' && renderSkillSubmenu && (
          <div className="flex min-h-0 w-full flex-1 flex-col gap-2">
            <div className="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-text-3 hover:bg-fill-2"
                aria-label={localize('com_ui_go_back')}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setMobilePanel('root');
                }}
              >
                <Outlined.ArrowLeft size={18} />
              </button>
              <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-slate-200" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">
                {localize('com_linsight_add_skill')}
              </span>
            </div>
            {renderSkillSubmenu(() => setRootOpen(false))}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};