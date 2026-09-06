// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Textarea } from "@/components/bs-ui/input";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { getPresetFileContentApi, savePresetFileContentApi } from "@/controllers/API/finetune";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { useTranslation } from "react-i18next";

interface DataSetContentProps {
    onChange?: () => void;
}

export interface DataSetContentRef {
    open: (item: { id: string; name: string }, edit?: boolean) => void;
}

/** 数据集内容预览/编辑对话框 */
export const DataSetContent = forwardRef<DataSetContentRef, DataSetContentProps>(({ onChange }, ref) => {
    const { t } = useTranslation();
    const { message } = useToast();

    const [open, setOpen] = useState(false);
    const [item, setItem] = useState<{ id: string; name: string } | null>(null);
    const [content, setContent] = useState("");
    const [editing, setEditing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    useImperativeHandle(ref, () => ({
        open: (target, edit = false) => {
            setItem(target);
            setEditing(edit);
            setOpen(true);
            setLoading(true);
            captureAndAlertRequestErrorHoc(getPresetFileContentApi(target.id))
                .then((res) => setContent(res.content || ""))
                .finally(() => setLoading(false));
        },
    }));

    useEffect(() => {
        if (!open) {
            setItem(null);
            setContent("");
            setEditing(false);
        }
    }, [open]);

    const handleSave = () => {
        try {
            const data = JSON.parse(content);
            if (!Array.isArray(data) || !data.length
                || !data.every((row) => row && typeof row === "object" && "instruction" in row && "output" in row)) {
                throw new Error("shape");
            }
        } catch {
            message({ title: t("prompt"), variant: "error", description: [t("dataset.contentInvalid")] });
            return;
        }
        setSaving(true);
        captureAndAlertRequestErrorHoc(savePresetFileContentApi(item!.id, content))
            .then(() => {
                message({ title: t("prompt"), variant: "success", description: [t("dataset.saveSuccess")] });
                setEditing(false);
                onChange?.();
            })
            .catch(() => message({ title: t("prompt"), variant: "error", description: [t("dataset.saveFailed")] }))
            .finally(() => setSaving(false));
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent className="sm:max-w-[720px]">
                <DialogHeader>
                    <DialogTitle className="flex items-center justify-between pr-6">
                        <span className="truncate">{item?.name}</span>
                        <div className="flex gap-2">
                            {!editing && (
                                <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
                                    {t("dataset.edit")}
                                </Button>
                            )}
                            {editing && (
                                <>
                                    <Button variant="secondary" size="sm" disabled={saving} onClick={() => setEditing(false)}>
                                        {t("cancel")}
                                    </Button>
                                    <Button size="sm" disabled={saving} onClick={handleSave}>
                                        {t("dataset.save")}
                                    </Button>
                                </>
                            )}
                        </div>
                    </DialogTitle>
                </DialogHeader>
                <div className="py-2">
                    <Textarea
                        value={content}
                        onChange={(e) => setContent(e.target.value)}
                        readOnly={!editing}
                        className="h-[55vh] font-mono text-xs leading-5"
                        placeholder={loading ? "..." : ""}
                    />
                    <p className="text-xs text-gray-400 mt-1">
                        {editing ? t("dataset.contentInvalid") : t("dataset.preview")}
                    </p>
                </div>
            </DialogContent>
        </Dialog>
    );
});
