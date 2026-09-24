// @ts-strict-ignore

import { Button } from "@bisheng/ui";
import { FileSearch2, FolderOpen, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { bishengConfState } from "../store/atoms";
import { useRecoilState } from "recoil";
// Narrow imports on purpose: `~/Providers` and `~/components` are barrels that
// reach ESM-only deps, which breaks any test that merely loads this control
// (F065 design 坑 16).
import { useToastContext } from "~/Providers/ToastContext";
import { uploadFile, uploadFileWithProgress } from "~/api/apps";
import useLocalize from "~/hooks/useLocalize";
import { mergeSelectedFiles } from "../formFile/mergeSelectedFiles";
import type { FormFileEntry } from "../formFile/types";
import { KnowledgeFilePicker } from "./KnowledgeFilePicker";
import { resolvePickedValues, type PickedKnowledgeFile } from "./KnowledgeFilePicker/resolvePickedValues";

/**
 * F065 — the file control of the workflow form.
 *
 * Two things changed from the original control, both opt-in or shape-preserving:
 * 1. Multi-select now MERGES across picks (append + name de-duplication) instead
 *    of replacing the list; single-select still keeps only the newest file.
 * 2. With `knowledgePicker`, files can be chosen from a knowledge space and end
 *    up in the same list as local uploads.
 *
 * Deliberately NOT changed: the two callback shapes. Single-select publishes a
 * scalar and multi-select an array, because `InputFormSkill` (which shares this
 * control, see design 坑 14) forwards the value straight into its payload — a
 * shape change there would silently alter that form's wire contract (AC-28).
 *
 * Unused legacy props, kept so callers need no edit: `value` (both callers pass
 * `''`; the list below is the source of truth) and `fileTypes` (superseded by
 * `suffixes`).
 */
export default function InputFileComponent({
    onChange,
    disabled = false,
    suffixes = [],
    placeholder = 'The current file is empty',
    onFileChange,
    editNode = false,
    isSSO = false,
    multiple = false,
    flow,
    /**
     * Publishes the selected entries themselves — the only channel that carries
     * the local `File` objects, which the submit-time archive step needs (design
     * 决策 5). Optional: callers that only care about names/values can omit it.
     */
    onEntriesChange,
    /**
     * Opt-in "choose from a knowledge space" affordance. Defaults to off: this
     * control is shared with the skill form, which keeps its current behaviour
     * (design 决策 9).
     */
    knowledgePicker = false,
}: {
    onChange: (value: string | string[]) => void;
    disabled?: boolean;
    suffixes?: string[];
    placeholder?: string;
    onFileChange: (value: string | string[]) => void;
    editNode?: boolean;
    isSSO?: boolean;
    multiple?: boolean;
    flow: { id?: string; name?: string };
    onEntriesChange?: (entries: FormFileEntry[]) => void;
    knowledgePicker?: boolean;
}) {
    const t = useLocalize()
    const [entries, setEntries] = useState<FormFileEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [pickerOpen, setPickerOpen] = useState(false);
    // Mirrors `entries` so async upload callbacks merge into the latest list
    // rather than the one captured when they started.
    const entriesRef = useRef<FormFileEntry[]>([]);

    const [bishengConfig] = useRecoilState(bishengConfState)

    const { showToast } = useToastContext();

    /**
     * Publish a list to the host form, keeping both channels in step (AC-13).
     * Single-select stays a scalar, multi-select an array — see the file header.
     */
    const commit = (next: FormFileEntry[]) => {
        entriesRef.current = next;
        setEntries(next);
        onEntriesChange?.(next);
        const names = next.map((entry) => entry.name);
        const values = next.map((entry) => entry.value);
        if (multiple) {
            onChange(names);
            onFileChange(values);
        } else {
            onChange(names[0] ?? '');
            onFileChange(values[0] ?? '');
        }
    }

    useEffect(() => {
        if (!disabled) return;
        // Return the previous reference when already empty: a fresh `[]` on every
        // tick would re-render forever, because `onChange` has an unstable
        // identity here and so re-runs this effect on each render.
        setEntries((prev) => (prev.length === 0 ? prev : []));
        entriesRef.current = [];
        onEntriesChange?.([]);
        onChange(multiple ? [] : '');
        onFileChange(multiple ? [] : '');
    }, [disabled, multiple, onChange, onFileChange, onEntriesChange]);

    const checkFileSize = (file) => {
        const maxSize = (bishengConfig?.uploaded_files_maximum_size || 50) * 1024 * 1024;
        if (file.size > maxSize) {
            return t('com_inputfile_exceed_limit', { 0: file.name, 1: (bishengConfig?.uploaded_files_maximum_size || 50) })
        }
        return ''
    }

    /**
     * Upload one local file and return its entry, keeping the `File` object on
     * it: the submit-time archive step re-uploads it to pre-check duplicates,
     * which is impossible once the blob is gone (design 决策 5 / 坑 12).
     */
    const uploadOne = (file: File): Promise<FormFileEntry> => {
        if (isSSO) {
            return uploadFileWithProgress(file, () => { }).then((res) => {
                if (typeof res === 'string' || !res?.file_path) {
                    throw new Error(typeof res === 'string' && res ? res : t('com_inputfiles_upload_failed', { 0: file.name }));
                }
                return { name: file.name, value: res.file_path, source: 'local', file } as FormFileEntry;
            });
        }
        return uploadFile(file, flow.id).then((data) => {
            const filePath = data?.data?.file_path;
            if (!filePath) throw new Error(t('com_inputfiles_upload_failed', { 0: file.name }));
            return { name: file.name, value: filePath, source: 'local', file } as FormFileEntry;
        });
    }

    const handleLocalPick = (picked: FileList | null) => {
        const files = picked ? Array.from(picked) : [];
        if (!files.length) return;

        const errors = files.map((file) => checkFileSize(file)).filter(Boolean);
        const accepted = files.filter((file) => !checkFileSize(file));
        if (errors.length) {
            showToast({ message: errors.join('\n'), status: 'error' });
            if (!accepted.length) return;
        }

        setLoading(true);
        Promise.allSettled(accepted.map((file) => uploadOne(file))).then((results) => {
            const uploaded: FormFileEntry[] = [];
            const failures: string[] = [];
            results.forEach((result, index) => {
                if (result.status === 'fulfilled') uploaded.push(result.value);
                else failures.push(result.reason?.message || accepted[index].name);
            });
            if (failures.length) {
                showToast({ message: failures.join('\n'), status: 'error' });
            }
            if (uploaded.length) {
                // Merge, don't replace: multi-select accumulates and drops name
                // collisions; single-select keeps only the newest (AC-09 ~ AC-12).
                commit(mergeSelectedFiles(entriesRef.current, uploaded, multiple));
            }
            setLoading(false);
        });
    }

    const handleButtonClick = () => {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = suffixes.join(",");
        input.style.display = "none";
        input.multiple = !!multiple;
        input.onchange = (e: Event) => handleLocalPick((e.target as HTMLInputElement).files);
        input.click();
    }

    const handlePickedConfirm = (picked: PickedKnowledgeFile[]) => {
        setPickerOpen(false);
        if (!picked.length) return;
        setLoading(true);
        resolvePickedValues(picked)
            .then(({ entries: resolved, failed }) => {
                if (failed.length) {
                    showToast({
                        message: failed.map((name) => t('com_knowledge.pick_resolve_failed', { 0: name })).join('\n'),
                        status: 'error',
                    });
                }
                if (resolved.length) {
                    commit(mergeSelectedFiles(entriesRef.current, resolved, multiple));
                }
            })
            .finally(() => setLoading(false));
    }

    const displayValue = entries.map((entry) => entry.name).join(',');

    return (
        <div className={disabled ? "input-component-div" : "w-full"}>
            <div className="input-file-component flex items-center gap-2 border bg-search-input rounded-md px-2 justify-between">
                <span
                    data-testid="file-pick-trigger"
                    onClick={handleButtonClick}
                    className={
                        editNode
                            ? "input-edit-node input-dialog text-muted-foreground"
                            : disabled
                                ? "input-disable input-dialog input-primary"
                                : "w-full input-dialog input-primary text-muted-foreground cursor-pointer"
                    }
                >
                    {displayValue !== "" ? displayValue : placeholder}
                </span>
                {knowledgePicker && (
                    <Button
                        size="icon"
                        variant="ghost"
                        disabled={disabled}
                        data-testid="knowledge-picker-trigger"
                        aria-label={t('com_knowledge.pick_from_space')}
                        title={t('com_knowledge.pick_from_space')}
                        onClick={() => setPickerOpen(true)}
                    >
                        <FolderOpen strokeWidth={1.5} size={18} />
                    </Button>
                )}
                <Button size="icon" variant="ghost" onClick={handleButtonClick}>
                    {!editNode && !loading && (
                        <FileSearch2
                            strokeWidth={1.5}
                            size={18}
                            className={
                                (disabled ? " text-ring " : " hover:text-accent-foreground")
                            }
                        />
                    )}
                    {!editNode && loading && (<Loader2 className="text-primary animate-spin duration-300 pointer-events-none" />)}
                </Button>
            </div>
            {knowledgePicker && (
                <KnowledgeFilePicker
                    open={pickerOpen}
                    multiple={!!multiple}
                    // Same list the local `<input accept>` gets, so a file this
                    // field would refuse on upload cannot be picked from a space
                    // either (AC-29).
                    suffixes={suffixes}
                    onClose={() => setPickerOpen(false)}
                    onConfirm={handlePickedConfirm}
                />
            )}
        </div>
    );
}
