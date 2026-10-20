/**
 * F087 collaborator picker (redesigned 2026-10-16).
 *
 * Enabled (a NEW task): a model-selector-style dropdown over the user's saved
 * member library —
 *   checkbox = the member joins THIS task,
 *   star     = auto-carried (pre-checked) into new conversations,
 *   pencil   = edit in the sheet (saved straight back to the library),
 *   「新建成员」creates AND saves in one step (no separate save-to-library).
 *
 * Disabled (follow-up / running task): read-only sheet with the locked config.
 * ``summary`` variant: the chip strip embedded in a finished task turn.
 */
import { Button } from '@bisheng/ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Pencil, Star, Users } from 'lucide-react';
import { useState } from 'react';
import { deleteSavedMember, listSavedMembers, saveMember, setMemberDefault } from '~/api/linsight';
import { NotificationSeverity } from '~/common';
import { Checkbox } from '~/components/ui/Checkbox';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '~/components/ui';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/Sheet';
import { useLocalize } from '~/hooks';
import { useToastContext } from '~/Providers';
import type { AgentConfig, HarnessOptions, SavedMember, SavedMemberPayload, SubagentConfig } from '~/types/harness';
import { generateUUID } from '~/utils';
import { draftFromSaved, isSavedDraft, savedIdOfDraft } from './collaboratorUtils';
import { SubagentEditor } from './SubagentEditor';

interface SubagentPickerProps {
    value?: AgentConfig | null;
    options: HarnessOptions;
    disabled?: boolean;
    onChange?: (value: AgentConfig) => void;
    /** `summary` renders the read-only strip used inside a task turn. */
    variant?: 'button' | 'summary';
}

/** Library-shaped payload (the local `lib-*` draft id never leaves the client). */
const memberPayload = (member: SubagentConfig): Omit<SavedMemberPayload, 'id'> => ({
    name: member.name,
    description: member.description,
    system_prompt: member.system_prompt,
    model_id: member.model_id,
    skill_names: member.skill_names,
    tool_refs: member.tool_refs,
    connector_refs: member.connector_refs,
});

const savedHasIssues = (row: SavedMember) =>
    Boolean(
        row.issues &&
            ((row.issues.tools?.length ?? 0) > 0 || (row.issues.skills?.length ?? 0) > 0 || row.issues.model),
    );

/**
 * Zero-tool member badge (2026-10-16 live run): a member with no tools and no
 * connectors can only read/write workspace files when delegated — it cannot
 * search or call business tools. That is a legitimate choice for a writing
 * role, so this is a hint only (never a save-blocking validation).
 */
const savedHasNoTools = (row: SavedMember) =>
    (row.tool_refs?.length ?? 0) === 0 && (row.connector_refs?.length ?? 0) === 0;

export function SubagentPicker({ value, options, disabled = false, onChange, variant = 'button' }: SubagentPickerProps) {
    const t = useLocalize();
    const { showToast } = useToastContext();
    const queryClient = useQueryClient();
    const members = value?.subagents || [];

    // Editor sheet (enabled flow) + read-only sheet (disabled flow).
    const [editorOpen, setEditorOpen] = useState(false);
    const [editing, setEditing] = useState<SubagentConfig | null>(null);
    const [editingSavedId, setEditingSavedId] = useState<number | null>(null);
    const [readOpen, setReadOpen] = useState(false);
    const [readDraft, setReadDraft] = useState<SubagentConfig[]>([]);
    const [readEditing, setReadEditing] = useState<string | null>(null);
    const readActive = readDraft.find((member) => member.id === readEditing);

    const savedQuery = useQuery({
        queryKey: ['linsight', 'savedMembers'],
        queryFn: listSavedMembers,
        enabled: !disabled,
        staleTime: 30_000,
    });
    const library = savedQuery.data || [];
    const invalidateSaved = () => queryClient.invalidateQueries({ queryKey: ['linsight', 'savedMembers'] });

    const publish = (next: SubagentConfig[]) => onChange?.({ schema_version: 1, subagents: next });
    const memberForSaved = (row: SavedMember) =>
        members.find((member) => savedIdOfDraft(member) === row.id || (!isSavedDraft(member) && member.name === row.name));

    // Legacy drafts (pre-library `agent-*` ids) that match no library row stay
    // visible and uncheckable-off — hidden config would be unmanageable.
    const orphans = members.filter(
        (member) => !isSavedDraft(member) && !library.some((row) => row.name === member.name),
    );

    const setDefaultMutation = useMutation({
        mutationFn: (row: SavedMember) => setMemberDefault(row.id, !row.is_default),
        onSuccess: invalidateSaved,
        onError: () => showToast?.({ message: t('harness.saveFailed'), severity: NotificationSeverity.ERROR }),
    });
    const saveMutation = useMutation({
        mutationFn: (args: { member: SubagentConfig; savedId: number | null }) =>
            saveMember({ ...memberPayload(args.member), id: args.savedId ?? undefined }),
        onSuccess: (saved, variables) => {
            invalidateSaved();
            // New member: auto-check it into this task. Edited member: refresh
            // its checked draft in place (if it was checked).
            if (variables.savedId == null) {
                publish([...members, draftFromSaved(saved)]);
            } else {
                publish(members.map((member) => (savedIdOfDraft(member) === saved.id ? draftFromSaved(saved) : member)));
            }
            setEditorOpen(false);
            setEditing(null);
            showToast?.({ message: t('harness.savedOk'), severity: NotificationSeverity.SUCCESS });
        },
        onError: (error: unknown) => {
            const serverMessage = (error as { response?: { data?: { status_message?: string } } })?.response?.data
                ?.status_message;
            showToast?.({ message: serverMessage || t('harness.saveFailed'), severity: NotificationSeverity.ERROR });
        },
    });
    const deleteMutation = useMutation({
        mutationFn: (memberId: number) => deleteSavedMember(memberId),
        onSuccess: (_res, memberId) => {
            invalidateSaved();
            publish(members.filter((member) => savedIdOfDraft(member) !== memberId));
            setEditorOpen(false);
            setEditing(null);
        },
        onError: () => showToast?.({ message: t('harness.deleteFailed'), severity: NotificationSeverity.ERROR }),
    });

    const blankMember = (): SubagentConfig => ({
        id: `agent-${generateUUID(12)}`,
        name: '',
        description: '',
        system_prompt: '',
        model_id: null,
        skill_names: [],
        tool_refs: [],
        connector_refs: [],
    });
    const openNew = () => {
        setEditing(blankMember());
        setEditingSavedId(null);
        setEditorOpen(true);
    };
    const openEdit = (row: SavedMember) => {
        setEditing(draftFromSaved(row));
        setEditingSavedId(row.id);
        setEditorOpen(true);
    };
    const toggleSaved = (row: SavedMember) => {
        const current = memberForSaved(row);
        if (current) publish(members.filter((member) => member !== current));
        else publish([...members, draftFromSaved(row)]);
    };

    const handleOpenRead = (next: boolean) => {
        if (next) {
            setReadDraft(members.map((member) => ({ ...member })));
            setReadEditing(null);
        }
        setReadOpen(next);
    };

    if (variant === 'summary') {
        return (
            <div className="flex w-full flex-wrap items-center gap-2" data-testid="collaborators-summary">
                <span className="text-caption text-text-3">{t('harness.members')}</span>
                {members.map((member) => (
                    <button
                        key={member.id}
                        type="button"
                        title={member.description}
                        onClick={() => handleOpenRead(true)}
                        className="max-w-[240px] truncate rounded-full border border-border-base bg-fill-1 px-2.5 py-0.5 text-caption text-text-1 hover:bg-fill-2"
                    >
                        {member.name}
                    </button>
                ))}
                <Sheet open={readOpen} onOpenChange={handleOpenRead}>
                    <SheetContent className="flex w-full flex-col gap-0 sm:max-w-[480px]">
                        <SheetHeader className="border-b border-border-base p-5">
                            <SheetTitle>{readActive ? readActive.name : t('harness.members')}</SheetTitle>
                            <SheetDescription>{t('harness.readOnly')}</SheetDescription>
                        </SheetHeader>
                        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
                            {readActive ? (
                                <>
                                    <Button type="button" color="default" variant="text" size="small" onClick={() => setReadEditing(null)}>
                                        {t('harness.back')}
                                    </Button>
                                    <SubagentEditor member={readActive} options={options} disabled onChange={() => undefined} />
                                </>
                            ) : (
                                readDraft.map((member) => (
                                    <div key={member.id} className="rounded-xl border border-border-base bg-fill-1 p-4">
                                        <p className="text-body font-medium text-text-1">{member.name}</p>
                                        <p className="mt-1 text-body-sm text-text-3">{member.description}</p>
                                        <div className="mt-3 flex gap-2">
                                            <Button type="button" color="default" variant="text" size="small" onClick={() => setReadEditing(member.id)}>
                                                {t('harness.view')}
                                            </Button>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </SheetContent>
                </Sheet>
            </div>
        );
    }

    if (disabled) {
        // Follow-up / running task: the configuration is locked; keep the
        // read-only inspection path (no library, no editing).
        return (
            <>
                <Button type="button" color="default" variant="text" size="small" className="gap-1" onClick={() => handleOpenRead(true)}>
                    <Users size={14} />
                    {t('harness.members')}
                    {members.length > 0 && (
                        <span className="rounded-full bg-blue-500/[0.07] px-1.5 text-caption text-text-2">{members.length}</span>
                    )}
                </Button>
                <Sheet open={readOpen} onOpenChange={handleOpenRead}>
                    <SheetContent className="flex w-full flex-col gap-0 sm:max-w-[480px]">
                        <SheetHeader className="border-b border-border-base p-5">
                            <SheetTitle>{readActive ? readActive.name : t('harness.members')}</SheetTitle>
                            <SheetDescription>{t('harness.readOnly')}</SheetDescription>
                        </SheetHeader>
                        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
                            {readActive ? (
                                <>
                                    <Button type="button" color="default" variant="text" size="small" onClick={() => setReadEditing(null)}>
                                        {t('harness.back')}
                                    </Button>
                                    <SubagentEditor member={readActive} options={options} disabled onChange={() => undefined} />
                                </>
                            ) : (
                                readDraft.map((member) => (
                                    <div key={member.id} className="rounded-xl border border-border-base bg-fill-1 p-4">
                                        <p className="text-body font-medium text-text-1">{member.name}</p>
                                        <p className="mt-1 text-body-sm text-text-3">{member.description}</p>
                                        <div className="mt-3 flex gap-2">
                                            <Button type="button" color="default" variant="text" size="small" onClick={() => setReadEditing(member.id)}>
                                                {t('harness.view')}
                                            </Button>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </SheetContent>
                </Sheet>
            </>
        );
    }

    return (
        <>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        className="flex h-8 max-w-[220px] items-center gap-1 rounded-[4px] px-2 text-text-2 outline-none hover:bg-black/5"
                        data-testid="collaborators-trigger"
                    >
                        <Users size={14} className="shrink-0" />
                        <span className="min-w-0 truncate text-[13px]">{t('harness.members')}</span>
                        {members.length > 0 && (
                            <span className="rounded-full bg-blue-500/[0.07] px-1.5 text-caption text-text-2">{members.length}</span>
                        )}
                        <ChevronDown size={13} className="shrink-0 opacity-60" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[320px] bg-white">
                    <DropdownMenuLabel className="text-caption font-normal text-text-3">
                        {t('harness.collaboratorHint')}
                    </DropdownMenuLabel>
                    {savedQuery.isLoading && (
                        <DropdownMenuItem disabled className="text-caption text-text-3">
                            {t('harness.loading')}
                        </DropdownMenuItem>
                    )}
                    {orphans.map((member) => (
                        <DropdownMenuItem key={member.id} onSelect={(event) => event.preventDefault()} className="flex items-center gap-2">
                            <Checkbox checked aria-label={member.name} onCheckedChange={() => publish(members.filter((item) => item !== member))} />
                            <span className="min-w-0 flex-1 truncate">{member.name}</span>
                        </DropdownMenuItem>
                    ))}
                    {library.map((row) => (
                        <DropdownMenuItem
                            key={row.id}
                            onSelect={(event) => event.preventDefault()}
                            className="flex items-center gap-2"
                        >
                            <Checkbox checked={Boolean(memberForSaved(row))} aria-label={row.name} onCheckedChange={() => toggleSaved(row)} />
                            <span className="min-w-0 flex-1 truncate">{row.name}</span>
                            {savedHasNoTools(row) && (
                                <span
                                    className="shrink-0 rounded bg-fill-2 px-1 text-caption text-text-3"
                                    title={t('harness.noToolsHint')}
                                    data-testid="member-no-tools"
                                >
                                    {t('harness.noTools')}
                                </span>
                            )}
                            {savedHasIssues(row) && (
                                <span className="shrink-0 text-caption text-warning" title={t('harness.savedIssues')}>
                                    !
                                </span>
                            )}
                            <button
                                type="button"
                                title={t('harness.defaultHint')}
                                aria-label={t('harness.defaultHint')}
                                className="shrink-0 rounded p-0.5 hover:bg-fill-2"
                                onClick={() => setDefaultMutation.mutate(row)}
                            >
                                <Star size={13} className={row.is_default ? 'fill-current text-warning' : 'text-text-3'} />
                            </button>
                            <button
                                type="button"
                                title={t('harness.edit')}
                                aria-label={t('harness.edit')}
                                className="shrink-0 rounded p-0.5 hover:bg-fill-2"
                                onClick={() => openEdit(row)}
                            >
                                <Pencil size={13} className="text-text-3" />
                            </button>
                        </DropdownMenuItem>
                    ))}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={openNew}>{t('harness.newMember')}</DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <Sheet
                open={editorOpen}
                onOpenChange={(next) => {
                    setEditorOpen(next);
                    if (!next) {
                        setEditing(null);
                        setEditingSavedId(null);
                    }
                }}
            >
                <SheetContent className="flex w-full flex-col gap-0 sm:max-w-[480px]">
                    <SheetHeader className="border-b border-border-base p-5">
                        <SheetTitle>{editing?.name || t('harness.newMember')}</SheetTitle>
                        <SheetDescription>{t('harness.autoSaveHint')}</SheetDescription>
                    </SheetHeader>
                    <div className="min-h-0 flex-1 overflow-y-auto p-5">
                        {editing && <SubagentEditor member={editing} options={options} onChange={setEditing} />}
                    </div>
                    <div className="flex items-center justify-between gap-2 border-t border-border-base p-4">
                        {editingSavedId != null ? (
                            <Button
                                type="button"
                                color="danger"
                                variant="text"
                                size="small"
                                disabled={deleteMutation.isLoading}
                                onClick={() => deleteMutation.mutate(editingSavedId)}
                            >
                                {t('harness.deleteSaved')}
                            </Button>
                        ) : (
                            <span />
                        )}
                        <Button
                            type="button"
                            disabled={
                                !editing ||
                                !editing.name.trim() ||
                                !editing.description.trim() ||
                                saveMutation.isLoading
                            }
                            onClick={() => editing && saveMutation.mutate({ member: editing, savedId: editingSavedId })}
                        >
                            {t('harness.save')}
                        </Button>
                    </div>
                </SheetContent>
            </Sheet>
        </>
    );
}
