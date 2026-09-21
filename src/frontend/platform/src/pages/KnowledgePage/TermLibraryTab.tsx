import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { TreeDepartmentSelect } from "@/components/bs-comp/department";
import {
    createTermApi,
    deleteTermApi,
    getTermsApi,
    importTermsApi,
    previewTermsImportApi,
    updateTermApi,
    type Term,
    type TermImportMode,
    type TermImportPreview,
} from "@/controllers/API/termLibrary";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { userContext } from "@/contexts/userContext";
import { Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE = 20;

/** Spec AC-04. Mirrors the backend's limit so the counter can warn before saving. */
const MAX_DEFINITION_LENGTH = 200;

/**
 * A term's aliases, as the editor shows them.
 *
 * The library stores aliases as a JSON array but the editor is one text field:
 * the same comma/semicolon convention the import file uses, so a term typed here
 * and the same term imported from a spreadsheet read alike.
 */
const ALIAS_SEPARATOR = /[,，;；|、]+/;

function parseAliases(text: string): string[] {
    const aliases: string[] = [];
    for (const token of text.split(ALIAS_SEPARATOR)) {
        const alias = token.trim();
        if (alias && !aliases.includes(alias)) aliases.push(alias);
    }
    return aliases;
}

interface TermDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** null = create; a term = edit. */
    editing: Term | null;
    onSaved: () => void;
}

/** Create/edit one term (spec AC-01 ~ AC-04). */
function TermDialog({ open, onOpenChange, editing, onSaved }: TermDialogProps) {
    const { t } = useTranslation();
    // The success toast belongs to the parent, which owns the list refresh: a
    // toast here as well would fire twice for one save.
    const [standardTerm, setStandardTerm] = useState("");
    const [aliasText, setAliasText] = useState("");
    const [definition, setDefinition] = useState("");
    const [departmentId, setDepartmentId] = useState<number | null>(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setStandardTerm(editing?.standard_term ?? "");
        setAliasText((editing?.aliases ?? []).join("、"));
        setDefinition(editing?.definition ?? "");
        setDepartmentId(editing?.department_id ?? null);
        setBusy(false);
    }, [open, editing]);

    const handleSave = async () => {
        setBusy(true);
        const payload = {
            standard_term: standardTerm.trim(),
            definition: definition.trim(),
            aliases: parseAliases(aliasText),
            department_id: departmentId,
        };
        const result = editing
            ? await captureAndAlertRequestErrorHoc(updateTermApi(editing.id, payload))
            : await captureAndAlertRequestErrorHoc(createTermApi(payload));
        setBusy(false);
        if (!result) return;
        onSaved();
        onOpenChange(false);
    };

    const canSave =
        standardTerm.trim().length > 0 &&
        definition.trim().length > 0 &&
        definition.trim().length <= MAX_DEFINITION_LENGTH;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{editing ? t("termLibrary.editTitle") : t("termLibrary.createTitle")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div>
                        <Label htmlFor="term-standard">{t("termLibrary.standardTerm")}</Label>
                        <Input
                            id="term-standard"
                            className="mt-2"
                            value={standardTerm}
                            maxLength={255}
                            placeholder={t("termLibrary.standardTermPlaceholder")}
                            onChange={(event) => setStandardTerm(event.target.value)}
                        />
                    </div>

                    <div>
                        <Label htmlFor="term-aliases">{t("termLibrary.aliases")}</Label>
                        <Input
                            id="term-aliases"
                            className="mt-2"
                            value={aliasText}
                            placeholder={t("termLibrary.aliasesPlaceholder")}
                            onChange={(event) => setAliasText(event.target.value)}
                        />
                        {/* Uniqueness spans the standard term AND every alias
                            (AC-02/AC-03), so a collision reported by the backend
                            (11503) will name whichever of the two clashed. */}
                        <p className="mt-1 text-xs text-[#86909C]">{t("termLibrary.aliasesHint")}</p>
                    </div>

                    <div>
                        <Label htmlFor="term-definition">{t("termLibrary.definition")}</Label>
                        <Textarea
                            id="term-definition"
                            className="mt-2"
                            rows={4}
                            value={definition}
                            onChange={(event) => setDefinition(event.target.value)}
                        />
                        <p className="mt-1 text-xs text-[#86909C]">
                            {t("termLibrary.definitionCounter", {
                                count: definition.trim().length,
                                max: MAX_DEFINITION_LENGTH,
                            })}
                        </p>
                    </div>

                    <div>
                        <Label>{t("termLibrary.department")}</Label>
                        {/* `modal={false}`: this select is nested in a Dialog, and
                            Radix's nested focus traps make the tree unusable
                            otherwise (see TreeDepartmentSelect). */}
                        <div className="mt-2">
                            <TreeDepartmentSelect
                                modal={false}
                                allowNone
                                noneLabel={t("termLibrary.departmentNone")}
                                value={departmentId}
                                placeholder={t("termLibrary.departmentPlaceholder")}
                                onChange={(id) => setDepartmentId(id)}
                            />
                        </div>
                        {/* INV-17: the department only groups the list. It is not a
                            permission and it does not narrow which terms reach a
                            conversation — saying so here is the only place an admin
                            would look for it. */}
                        <p className="mt-1 text-xs text-[#86909C]">{t("termLibrary.departmentHint")}</p>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        {t("cancel")}
                    </Button>
                    <Button disabled={!canSave || busy} onClick={handleSave}>
                        {t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

interface ImportDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onImported: () => void;
}

/**
 * Pick a file, see what WOULD change, then confirm (spec AC-07 ~ AC-09).
 *
 * The preview step exists because overwrite replaces the entire library: an
 * admin who picks the wrong export would otherwise wipe terms nobody meant to
 * remove, and the only warning would have been the word "overwrite".
 */
function ImportDialog({ open, onOpenChange, onImported }: ImportDialogProps) {
    const { t } = useTranslation();
    const { toast } = useToast();
    const fileInputId = useRef(`term-import-file-${Math.random().toString(36).slice(2)}`).current;
    const [file, setFile] = useState<File | null>(null);
    const [mode, setMode] = useState<TermImportMode>("append");
    const [preview, setPreview] = useState<TermImportPreview | null>(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setFile(null);
        // Append is the default here, unlike the platform word library: a term
        // carries a definition someone wrote, so the destructive mode has to be
        // chosen deliberately rather than fallen into.
        setMode("append");
        setPreview(null);
        setBusy(false);
    }, [open]);

    const handlePreview = async () => {
        if (!file) return;
        setBusy(true);
        const result = await captureAndAlertRequestErrorHoc(previewTermsImportApi(file, mode));
        setBusy(false);
        if (result) setPreview(result);
    };

    const handleImport = async () => {
        if (!file) return;
        setBusy(true);
        const result = await captureAndAlertRequestErrorHoc(importTermsApi(file, mode));
        setBusy(false);
        if (!result) return;
        toast({
            title: t("prompt"),
            variant: "success",
            description: t("termLibrary.imported", { count: result.added }),
        });
        onImported();
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>{t("termLibrary.importTitle")}</DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div>
                        <Label htmlFor={fileInputId}>{t("termLibrary.importFile")}</Label>
                        {/* csv / xlsx only — a term needs three columns and a
                            plain word list cannot carry a definition (design
                            decision 8). The backend refuses txt outright; the
                            accept list keeps the file picker honest about it. */}
                        <input
                            id={fileInputId}
                            type="file"
                            accept=".csv,.xlsx"
                            className="mt-2 block w-full text-sm"
                            onChange={(event) => {
                                setFile(event.target.files?.[0] ?? null);
                                setPreview(null);
                            }}
                        />
                        <p className="mt-1 text-xs text-[#86909C]">{t("termLibrary.importFileHint")}</p>
                    </div>

                    <div>
                        <Label>{t("termLibrary.importMode")}</Label>
                        <div className="mt-2 flex gap-4">
                            {(["append", "overwrite"] as TermImportMode[]).map((value) => (
                                <label key={value} className="flex cursor-pointer items-center gap-2 text-sm">
                                    <input
                                        type="radio"
                                        name="term-import-mode"
                                        checked={mode === value}
                                        onChange={() => {
                                            setMode(value);
                                            setPreview(null);
                                        }}
                                    />
                                    {value === "overwrite" ? t("termLibrary.modeOverwrite") : t("termLibrary.modeAppend")}
                                </label>
                            ))}
                        </div>
                        <p className="mt-1 text-xs text-[#86909C]">
                            {mode === "overwrite" ? t("termLibrary.modeOverwriteHint") : t("termLibrary.modeAppendHint")}
                        </p>
                    </div>

                    {preview && (
                        <div className="rounded-lg border border-[#ECECEC] bg-[#FAFBFC] p-3 text-sm">
                            <div className="grid grid-cols-2 gap-y-1">
                                <span>{t("termLibrary.previewAdded")}</span>
                                <span className="font-medium text-green-600">+{preview.added}</span>
                                {mode === "overwrite" && (
                                    <>
                                        <span>{t("termLibrary.previewRemoved")}</span>
                                        <span className="font-medium text-red-500">-{preview.removed}</span>
                                    </>
                                )}
                                <span>{t("termLibrary.previewDuplicates")}</span>
                                <span className="font-medium">{preview.duplicates}</span>
                                {preview.dropped_overlong > 0 && (
                                    <>
                                        <span>{t("termLibrary.previewDropped")}</span>
                                        <span className="font-medium text-amber-600">{preview.dropped_overlong}</span>
                                    </>
                                )}
                            </div>
                            {mode === "overwrite" && preview.removed > 0 && (
                                <p className="mt-2 text-xs text-red-500">
                                    {t("termLibrary.overwriteWarning", { count: preview.removed })}
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
                            {t("termLibrary.previewAction")}
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

export interface TermLibraryTabProps {
    /** Numeric department id from the page-level tree, or null for "all". */
    departmentId: number | null;
    /** Whether the selected department includes its descendants (AC-13). */
    includeSubDepartments: boolean;
}

/**
 * The term library, as the third tab of the knowledge page (spec AC-12).
 *
 * Permissions match the API and the page it lives on: reaching this tab at all
 * requires the `knowledge` menu, and every write additionally requires
 * `create_knowledge` (AC-16 ~ AC-18). Hiding the buttons is an AFFORDANCE, not a
 * boundary — the backend rejects regardless, and 403s are handled by the
 * response interceptor, so there is deliberately no 403 branch in this file.
 */
export function TermLibraryTab({ departmentId, includeSubDepartments }: TermLibraryTabProps) {
    const { t } = useTranslation();
    const { toast } = useToast();
    const { user } = useContext(userContext);
    const [terms, setTerms] = useState<Term[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<Term | null>(null);
    const [importOpen, setImportOpen] = useState(false);

    // Admin bypasses the menu check, exactly as the create button does on the
    // file/QA tabs (KnowledgeFile.tsx:397).
    const canWrite = user?.role === "admin" || (user?.web_menu || []).includes("create_knowledge");

    const load = useCallback(async () => {
        setLoading(true);
        const result = await captureAndAlertRequestErrorHoc(
            getTermsApi({
                department_id: departmentId ?? undefined,
                include_sub_departments: includeSubDepartments,
                page,
                page_size: PAGE_SIZE,
            }),
        );
        setLoading(false);
        if (!result) return;
        setTerms(result.data);
        setTotal(result.total);
    }, [departmentId, includeSubDepartments, page]);

    useEffect(() => {
        load();
    }, [load]);

    // Changing the department filter restarts paging: page 3 of the old scope is
    // meaningless in the new one, and the backend would happily serve whatever
    // happened to be there.
    useEffect(() => {
        setPage(1);
    }, [departmentId, includeSubDepartments]);

    const handleDelete = (term: Term) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("termLibrary.deleteConfirm", { term: term.standard_term }),
            okTxt: t("confirm"),
            onOk: async () => {
                const result = await captureAndAlertRequestErrorHoc(deleteTermApi(term.id));
                if (result) load();
            },
        });
    };

    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <div className="mb-6 rounded-lg border border-[#ECECEC] bg-white p-5">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <p className="text-lg font-bold">{t("termLibrary.title")}</p>
                    <p className="mt-1 text-sm text-[#86909C]">{t("termLibrary.desc")}</p>
                </div>
                {canWrite && (
                    <div className="flex shrink-0 gap-2">
                        <Button variant="outline" size="sm" onClick={() => setImportOpen(true)}>
                            <Upload className="mr-1 size-4" />
                            {t("termLibrary.importAction")}
                        </Button>
                        <Button
                            size="sm"
                            onClick={() => {
                                setEditing(null);
                                setDialogOpen(true);
                            }}
                        >
                            <Plus className="mr-1 size-4" />
                            {t("termLibrary.createAction")}
                        </Button>
                    </div>
                )}
            </div>

            <div className="mt-4">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-[#ECECEC] text-left text-[#86909C]">
                            <th className="py-2 font-normal">{t("termLibrary.colTerm")}</th>
                            <th className="py-2 font-normal">{t("termLibrary.colAliases")}</th>
                            <th className="py-2 font-normal">{t("termLibrary.colDefinition")}</th>
                            <th className="py-2 font-normal">{t("termLibrary.colTime")}</th>
                            {canWrite && <th className="w-20 py-2 font-normal" />}
                        </tr>
                    </thead>
                    <tbody>
                        {terms.map((row) => (
                            <tr key={row.id} className="border-b border-[#F5F6F7]">
                                <td className="py-2 font-medium">{row.standard_term}</td>
                                <td className="py-2 text-[#86909C]">{row.aliases.length ? row.aliases.join("、") : "-"}</td>
                                <td className="max-w-[360px] py-2 text-[#4E5969]">{row.definition}</td>
                                <td className="py-2 whitespace-nowrap text-[#86909C]">
                                    {row.create_time?.replace("T", " ").slice(0, 19) || "-"}
                                </td>
                                {canWrite && (
                                    <td className="py-2">
                                        <div className="flex justify-end gap-1">
                                            <button
                                                type="button"
                                                className="rounded p-1 text-[#86909C] hover:bg-[#F5F6F7] hover:text-[#165DFF]"
                                                onClick={() => {
                                                    setEditing(row);
                                                    setDialogOpen(true);
                                                }}
                                            >
                                                <Pencil className="size-4" />
                                            </button>
                                            <button
                                                type="button"
                                                className="rounded p-1 text-[#86909C] hover:bg-[#F5F6F7] hover:text-red-500"
                                                onClick={() => handleDelete(row)}
                                            >
                                                <Trash2 className="size-4" />
                                            </button>
                                        </div>
                                    </td>
                                )}
                            </tr>
                        ))}
                        {!loading && terms.length === 0 && (
                            <tr>
                                <td colSpan={canWrite ? 5 : 4} className="py-8 text-center text-[#86909C]">
                                    {t("termLibrary.empty")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-[#86909C]">
                <span>{t("termLibrary.total", { count: total })}</span>
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

            <TermDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                editing={editing}
                onSaved={() => {
                    toast({ title: t("prompt"), variant: "success", description: t("termLibrary.saved") });
                    load();
                }}
            />
            <ImportDialog open={importOpen} onOpenChange={setImportOpen} onImported={load} />
        </div>
    );
}

export default TermLibraryTab;
