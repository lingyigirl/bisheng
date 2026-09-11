import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/bs-ui/dialog";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import {
    createKnowledgeTagApi,
    deleteKnowledgeTagApi,
    getKnowledgeTagsApi,
    KnowledgeTagItem,
    updateKnowledgeTagApi,
} from "@/controllers/API/knowledgeTags";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { truncateString } from "@/util/utils";
import { Check, PenLine, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface TagCenterDialogProps {
    knowledgeId: string | number;
    children: React.ReactNode;
    onChanged?: () => void;
}

const TAG_NAME_MAX_LENGTH = 100;
const TAG_DISPLAY_MAX_LENGTH = 18;

/**
 * F055 (AC-01/AC-04): standalone tag management dialog for a knowledge base.
 * Create / rename / delete tags with the existing knowledgeTags CRUD; deleting
 * a tag cascades to the backend TagLinks, so documents auto-detach (AC-04).
 */
export function TagCenterDialog({ knowledgeId, children, onChanged }: TagCenterDialogProps) {
    const { toast } = useToast();
    const { t } = useTranslation('knowledge');
    const [open, setOpen] = useState(false);
    const [tags, setTags] = useState<KnowledgeTagItem[]>([]);
    const [createValue, setCreateValue] = useState("");
    const [creating, setCreating] = useState(false);
    const [editingTagId, setEditingTagId] = useState<number | null>(null);
    const [editValue, setEditValue] = useState("");
    const [savingTagId, setSavingTagId] = useState<number | null>(null);
    const [deletingTagId, setDeletingTagId] = useState<number | null>(null);

    const numericKnowledgeId = Number(knowledgeId);

    const fetchTags = useCallback(async () => {
        if (!numericKnowledgeId) return;
        const response = await captureAndAlertRequestErrorHoc(
            getKnowledgeTagsApi(numericKnowledgeId, { page: 1, limit: 100 })
        );
        if (!response || response === 'canceled') return;
        setTags(response.data ?? []);
    }, [numericKnowledgeId]);

    useEffect(() => {
        if (!open) return;
        setCreateValue("");
        setEditingTagId(null);
        void fetchTags();
    }, [open, fetchTags]);

    const validateTagName = (value: string) => {
        const name = value.trim();
        if (!name) {
            toast({ variant: 'error', description: t('tagNameEmpty') });
            return false;
        }
        if (name.length > TAG_NAME_MAX_LENGTH) {
            toast({ variant: 'error', description: t('tagNameMaxLength', { count: TAG_NAME_MAX_LENGTH }) });
            return false;
        }
        return true;
    };

    const handleCreate = async () => {
        const name = createValue.trim();
        if (!validateTagName(name)) return;

        const existing = tags.find((tag) => tag.name.trim().toLowerCase() === name.toLowerCase());
        if (existing) {
            toast({ variant: 'warning', description: t('tagExists') });
            return;
        }

        setCreating(true);
        const result = await captureAndAlertRequestErrorHoc(createKnowledgeTagApi(numericKnowledgeId, name));
        setCreating(false);
        if (!result || result === 'canceled') return;

        // The request interceptor already unwraps the response envelope, so
        // `result` IS the created tag (double-unwrapping yields undefined).
        const newTag = result as KnowledgeTagItem;
        setTags(prev => [newTag, ...prev.filter(tag => tag.id !== newTag.id)]);
        setCreateValue("");
        toast({ variant: 'success', description: t('tagCreated') });
        onChanged?.();
    };

    const handleStartEdit = (tag: KnowledgeTagItem) => {
        setEditingTagId(tag.id);
        setEditValue(tag.name);
    };

    const handleCancelEdit = () => {
        setEditingTagId(null);
        setEditValue("");
    };

    const handleSaveEdit = async (tag: KnowledgeTagItem) => {
        const name = editValue.trim();
        if (!validateTagName(name)) return;
        if (name === tag.name) {
            handleCancelEdit();
            return;
        }
        // Mirror the create guard: renaming onto an existing tag name would
        // produce two same-name tags (backend also rejects with 10985).
        const duplicate = tags.find((t) => t.id !== tag.id && t.name.trim().toLowerCase() === name.toLowerCase());
        if (duplicate) {
            toast({ variant: 'warning', description: t('tagExists') });
            return;
        }

        setSavingTagId(tag.id);
        const result = await captureAndAlertRequestErrorHoc(
            updateKnowledgeTagApi(numericKnowledgeId, tag.id, name)
        );
        setSavingTagId(null);
        if (!result || result === 'canceled') return;

        const updatedTag = result as KnowledgeTagItem;
        setTags(prev => prev.map(item => (item.id === tag.id ? { ...item, ...updatedTag } : item)));
        handleCancelEdit();
        toast({ variant: 'success', description: t('tagUpdated') });
        onChanged?.();
    };

    const handleDelete = (tag: KnowledgeTagItem) => {
        if (deletingTagId !== null) return;
        bsConfirm({
            title: t('deleteTag'),
            desc: t('deleteTagConfirmDescription'),
            onOk(next) {
                setDeletingTagId(tag.id);
                captureAndAlertRequestErrorHoc(deleteKnowledgeTagApi(numericKnowledgeId, tag.id)).then((result) => {
                    setDeletingTagId(null);
                    if (result === false || result === 'canceled') return;
                    setTags(prev => prev.filter(item => item.id !== tag.id));
                    toast({ variant: 'success', description: t('tagDeleted') });
                    onChanged?.();
                });
                next();
            },
        });
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>{children}</DialogTrigger>
            <DialogContent
                className="w-[600px] max-w-[calc(100vw-32px)] gap-0 rounded-xl border-none p-0"
                onClick={(event) => event.stopPropagation()}
            >
                <DialogHeader className="px-6 py-4">
                    <DialogTitle className="text-[16px] font-medium leading-6 text-[#212121]">
                        {t('tagCenter')}
                    </DialogTitle>
                    <span className="text-[12px] leading-5 text-[#86909c]">{t('tagCenterDescription')}</span>
                </DialogHeader>
                <div className="flex flex-col gap-4 px-6 pb-3">
                    <div className="flex items-center gap-2">
                        <div className="relative flex h-9 flex-1 items-center rounded-[8px] border border-[#EBECF0] bg-white px-3 transition-colors focus-within:border-primary">
                            <input
                                type="text"
                                value={createValue}
                                maxLength={TAG_NAME_MAX_LENGTH}
                                onChange={(event) => setCreateValue(event.target.value)}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                        event.preventDefault();
                                        void handleCreate();
                                    }
                                }}
                                placeholder={t('createTagPlaceholder')}
                                className="h-full flex-1 bg-transparent text-sm leading-[22px] text-[#212121] outline-none placeholder-[#86909c]"
                            />
                        </div>
                        <button
                            type="button"
                            className="flex h-9 items-center gap-1 rounded-[6px] bg-[#004DA4] px-4 text-[14px] font-normal text-white transition-colors hover:bg-[#5A7EFF] disabled:cursor-not-allowed disabled:opacity-60"
                            onClick={() => void handleCreate()}
                            disabled={creating}
                        >
                            <Plus className="size-4" />
                            {t('createTag')}
                        </button>
                    </div>

                    <div className="flex flex-col gap-2 pt-1">
                        <div className="text-[14px] font-medium leading-5 text-[#212121]">{t('tags')}</div>
                        <div className="flex max-h-[360px] flex-col gap-1 overflow-y-auto">
                            {tags.length === 0 && (
                                <span className="py-2 text-[12px] text-[#86909c]">{t('noTags')}</span>
                            )}
                            {tags.map((tag) => {
                                const isEditing = editingTagId === tag.id;
                                const isSaving = savingTagId === tag.id;
                                const isDeleting = deletingTagId === tag.id;
                                return (
                                    <div
                                        key={tag.id}
                                        className="group/tag-center flex h-9 items-center justify-between rounded-[6px] px-2 hover:bg-[#f7f8fa]"
                                    >
                                        {isEditing ? (
                                            <div className="flex flex-1 items-center gap-2 pr-2">
                                                <input
                                                    type="text"
                                                    value={editValue}
                                                    maxLength={TAG_NAME_MAX_LENGTH}
                                                    onChange={(event) => setEditValue(event.target.value)}
                                                    onKeyDown={(event) => {
                                                        if (event.key === "Enter") {
                                                            event.preventDefault();
                                                            void handleSaveEdit(tag);
                                                        } else if (event.key === "Escape") {
                                                            handleCancelEdit();
                                                        }
                                                    }}
                                                    className="h-7 flex-1 rounded-[4px] border border-[#EBECF0] bg-white px-2 text-sm leading-[22px] text-[#212121] outline-none focus:border-primary"
                                                />
                                                <button
                                                    type="button"
                                                    className="flex size-6 items-center justify-center rounded-[4px] text-[#00b42a] hover:bg-[#e8ffea] disabled:cursor-not-allowed disabled:opacity-60"
                                                    onClick={() => void handleSaveEdit(tag)}
                                                    disabled={isSaving}
                                                >
                                                    <Check className="size-4" />
                                                </button>
                                                <button
                                                    type="button"
                                                    className="flex size-6 items-center justify-center rounded-[4px] text-[#86909c] hover:bg-[#f2f3f5]"
                                                    onClick={handleCancelEdit}
                                                >
                                                    <X className="size-4" />
                                                </button>
                                            </div>
                                        ) : (
                                            <>
                                                <span
                                                    className="max-w-[420px] truncate text-sm leading-[22px] text-[#1d2129]"
                                                    title={tag.name}
                                                >
                                                    {truncateString(tag.name, TAG_DISPLAY_MAX_LENGTH)}
                                                </span>
                                                <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover/tag-center:opacity-100">
                                                    <button
                                                        type="button"
                                                        className="flex size-6 items-center justify-center rounded-[4px] text-[#86909c] hover:bg-[#e5e6eb] hover:text-[#4e5969]"
                                                        onClick={() => handleStartEdit(tag)}
                                                        title={t('editTag')}
                                                    >
                                                        <PenLine className="size-3.5" />
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="flex size-6 items-center justify-center rounded-[4px] text-[#86909c] hover:bg-[#ffece8] hover:text-[#f53f3f] disabled:cursor-not-allowed disabled:text-[#c9cdd4]"
                                                        onClick={() => handleDelete(tag)}
                                                        disabled={isDeleting}
                                                        title={t('deleteTag')}
                                                    >
                                                        <Trash2 className="size-3.5" />
                                                    </button>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
