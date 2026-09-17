import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Input, SearchInput } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import {
    addPlatformWordApi,
    deletePlatformWordApi,
    getPlatformWordsApi,
    importPlatformWordsApi,
    previewPlatformWordsImportApi,
    type ImportMode,
    type ImportPreview,
    type PlatformWord,
} from "@/controllers/API/platformSensitiveWord";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE = 20;

interface ImportDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onImported: () => void;
}

/**
 * Import dialog: pick a file, see what WOULD change, then confirm.
 *
 * The preview step exists because overwrite mode replaces the whole library —
 * uploading a truncated or wrong-column export without seeing the numbers first
 * would silently wipe words nobody meant to remove (spec AC-03). The counts are
 * always shown for overwrite; for append they are informational.
 */
function ImportDialog({ open, onOpenChange, onImported }: ImportDialogProps) {
    const { t } = useTranslation();
    const { toast } = useToast();
    const fileInputId = useRef(`platform-words-file-${Math.random().toString(36).slice(2)}`).current;
    const [file, setFile] = useState<File | null>(null);
    const [mode, setMode] = useState<ImportMode>("overwrite");
    const [preview, setPreview] = useState<ImportPreview | null>(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setFile(null);
        setMode("overwrite");
        setPreview(null);
        setBusy(false);
    }, [open]);

    const handlePick = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const picked = event.target.files?.[0];
        if (!picked) return;
        setFile(picked);
        setPreview(null);
    };

    const handlePreview = async () => {
        if (!file) return;
        setBusy(true);
        const result = await captureAndAlertRequestErrorHoc(previewPlatformWordsImportApi(file, mode));
        setBusy(false);
        if (result) setPreview(result);
    };

    const handleImport = async () => {
        if (!file) return;
        setBusy(true);
        const result = await captureAndAlertRequestErrorHoc(importPlatformWordsApi(file, mode));
        setBusy(false);
        if (!result) return;
        toast({ title: t("prompt"), variant: "success", description: t("platformWords.imported", { count: result.written }) });
        onImported();
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t("platformWords.importTitle")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div>
                        <Label htmlFor={fileInputId}>{t("platformWords.importFile")}</Label>
                        <input
                            id={fileInputId}
                            type="file"
                            accept=".txt,.csv,.xlsx"
                            className="mt-2 block w-full text-sm"
                            onChange={handlePick}
                        />
                        <p className="mt-1 text-xs text-[#86909C]">{t("platformWords.importFileHint")}</p>
                    </div>

                    <div>
                        <Label>{t("platformWords.importMode")}</Label>
                        <div className="mt-2 flex gap-4">
                            {(["overwrite", "append"] as ImportMode[]).map((value) => (
                                <label key={value} className="flex cursor-pointer items-center gap-2 text-sm">
                                    <input
                                        type="radio"
                                        name="platform-word-import-mode"
                                        checked={mode === value}
                                        onChange={() => {
                                            setMode(value);
                                            setPreview(null);
                                        }}
                                    />
                                    {value === "overwrite" ? t("platformWords.modeOverwrite") : t("platformWords.modeAppend")}
                                </label>
                            ))}
                        </div>
                        <p className="mt-1 text-xs text-[#86909C]">
                            {mode === "overwrite" ? t("platformWords.modeOverwriteHint") : t("platformWords.modeAppendHint")}
                        </p>
                    </div>

                    {preview && (
                        <div className="rounded-lg border border-[#ECECEC] bg-[#FAFBFC] p-3 text-sm">
                            <div className="grid grid-cols-2 gap-y-1">
                                <span>{t("platformWords.previewIncoming")}</span>
                                <span className="font-medium">{preview.incoming}</span>
                                <span>{t("platformWords.previewAdded")}</span>
                                <span className="font-medium text-green-600">+{preview.added}</span>
                                <span>{t("platformWords.previewRemoved")}</span>
                                <span className="font-medium text-red-500">-{preview.removed}</span>
                                <span>{t("platformWords.previewDuplicates")}</span>
                                <span className="font-medium">{preview.duplicates}</span>
                                {preview.dropped_overlong > 0 && (
                                    <>
                                        <span>{t("platformWords.previewDropped")}</span>
                                        <span className="font-medium text-amber-600">{preview.dropped_overlong}</span>
                                    </>
                                )}
                            </div>
                            {preview.added_sample.length > 0 && (
                                <p className="mt-2 break-all text-xs text-[#86909C]">
                                    {t("platformWords.previewAddedSample")}: {preview.added_sample.join("、")}
                                </p>
                            )}
                        </div>
                    )}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        {t("cancel")}
                    </Button>
                    {!preview ? (
                        <Button disabled={!file || busy} onClick={handlePreview}>
                            {t("platformWords.previewAction")}
                        </Button>
                    ) : (
                        <Button disabled={busy} onClick={handleImport}>
                            {t("confirm")}
                        </Button>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

/** Platform-wide (cross-tenant) word library. Super admin only. */
export function PlatformSensitiveWords() {
    const { t } = useTranslation();
    const { toast } = useToast();
    const [words, setWords] = useState<PlatformWord[]>([]);
    const [total, setTotal] = useState(0);
    const [keyword, setKeyword] = useState("");
    const [page, setPage] = useState(1);
    const [newWord, setNewWord] = useState("");
    const [loading, setLoading] = useState(false);
    const [importOpen, setImportOpen] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await captureAndAlertRequestErrorHoc(
            getPlatformWordsApi({ keyword: keyword || undefined, page, page_size: PAGE_SIZE }),
        );
        setLoading(false);
        if (!result) return;
        setWords(result.data);
        setTotal(result.total);
    }, [keyword, page]);

    useEffect(() => {
        load();
    }, [load]);

    const handleAdd = async () => {
        const word = newWord.trim();
        if (!word) return;
        const result = await captureAndAlertRequestErrorHoc(addPlatformWordApi(word));
        if (!result) return;
        if (!result.created) {
            toast({ title: t("prompt"), variant: "warning", description: t("platformWords.alreadyExists") });
            return;
        }
        setNewWord("");
        load();
    };

    const handleDelete = (row: PlatformWord) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("platformWords.deleteConfirm", { word: row.word }),
            okTxt: t("confirm"),
            onOk: async () => {
                const result = await captureAndAlertRequestErrorHoc(deletePlatformWordApi(row.id));
                if (result) load();
            },
        });
    };

    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <div className="mb-6 rounded-lg border border-[#ECECEC] bg-white p-5">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <p className="text-lg font-bold">{t("platformWords.title")}</p>
                    <p className="mt-1 text-sm text-[#86909C]">{t("platformWords.desc")}</p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setImportOpen(true)}>
                    <Upload className="mr-1 size-4" />
                    {t("platformWords.importAction")}
                </Button>
            </div>

            <div className="mt-4 flex items-center gap-2">
                <Input
                    className="max-w-[280px]"
                    value={newWord}
                    placeholder={t("platformWords.addPlaceholder")}
                    onChange={(event) => setNewWord(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") handleAdd();
                    }}
                />
                <Button size="sm" onClick={handleAdd} disabled={!newWord.trim()}>
                    {t("platformWords.addAction")}
                </Button>
                <SearchInput
                    className="ml-auto max-w-[220px]"
                    placeholder={t("platformWords.searchPlaceholder")}
                    onChange={(event) => {
                        setKeyword(event.target.value);
                        setPage(1);
                    }}
                />
            </div>

            <div className="mt-4">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-[#ECECEC] text-left text-[#86909C]">
                            <th className="py-2 font-normal">{t("platformWords.colWord")}</th>
                            <th className="py-2 font-normal">{t("platformWords.colSource")}</th>
                            <th className="py-2 font-normal">{t("platformWords.colTime")}</th>
                            <th className="w-16 py-2 font-normal" />
                        </tr>
                    </thead>
                    <tbody>
                        {words.map((row) => (
                            <tr key={row.id} className="border-b border-[#F5F6F7]">
                                <td className="py-2">{row.word}</td>
                                <td className="py-2 text-[#86909C]">
                                    {row.source === "import"
                                        ? t("platformWords.sourceImport")
                                        : t("platformWords.sourceManual")}
                                </td>
                                <td className="py-2 text-[#86909C]">{row.create_time?.replace("T", " ").slice(0, 19) || "-"}</td>
                                <td className="py-2 text-right">
                                    <button
                                        type="button"
                                        className="rounded p-1 text-[#86909C] hover:bg-[#F5F6F7] hover:text-red-500"
                                        onClick={() => handleDelete(row)}
                                    >
                                        <Trash2 className="size-4" />
                                    </button>
                                </td>
                            </tr>
                        ))}
                        {!loading && words.length === 0 && (
                            <tr>
                                <td colSpan={4} className="py-8 text-center text-[#86909C]">
                                    {t("platformWords.empty")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-[#86909C]">
                <span>{t("platformWords.total", { count: total })}</span>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={page <= 1}
                        onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                    >
                        {t("previousPage")}
                    </Button>
                    <span>
                        {page} / {pageCount}
                    </span>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={page >= pageCount}
                        onClick={() => setPage((prev) => prev + 1)}
                    >
                        {t("nextPage")}
                    </Button>
                </div>
            </div>

            <ImportDialog open={importOpen} onOpenChange={setImportOpen} onImported={load} />
        </div>
    );
}
