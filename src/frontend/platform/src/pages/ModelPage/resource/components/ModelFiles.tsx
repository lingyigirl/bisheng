// @ts-strict-ignore
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Button } from "@/components/bs-ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { deleteModelFileApi, getModelFilesApi } from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { ModelFileInfo } from "@/types/api/gpu";
import { FolderIcon, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface ModelFilesProps {
    nodeId: number;
}

const fmtSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

export function ModelFiles({ nodeId }: ModelFilesProps) {
    const { t } = useTranslation();
    const { message } = useToast();
    // breadcrumb stack of directory names
    const [stack, setStack] = useState<string[]>([]);
    const [files, setFiles] = useState<ModelFileInfo[]>([]);
    const [loading, setLoading] = useState(false);

    const subpath = stack.join("/");

    const load = useCallback(() => {
        setLoading(true);
        captureAndAlertRequestErrorHoc(getModelFilesApi(nodeId, subpath))
            .then((res) => setFiles(res || []))
            .finally(() => setLoading(false));
    }, [nodeId, subpath]);

    useEffect(() => {
        if (nodeId) load();
    }, [nodeId, load]);

    const enterDir = (name: string) => setStack((prev) => [...prev, name]);

    const handleDelete = (file: ModelFileInfo) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("model.modelFileDeleteConfirm", { path: file.path }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteModelFileApi(nodeId, file.path)).then(() => {
                    message({ title: t("prompt"), variant: "success", description: [t("deleteSuccess")] });
                    load();
                    next();
                }).catch(() => next());
            },
        });
    };

    return (
        <div className="rounded-lg border bg-background">
            <div className="flex items-center justify-between px-3 py-2 border-b">
                <div className="flex items-center gap-1 text-sm flex-wrap">
                    <Button variant="ghost" size="sm" className="px-2" disabled={!stack.length} onClick={() => setStack([])}>
                        {t("model.modelFileRoot")}
                    </Button>
                    {stack.map((name, idx) => (
                        <span key={idx} className="flex items-center gap-1">
                            <span className="text-gray-400">/</span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="px-1"
                                onClick={() => setStack((prev) => prev.slice(0, idx + 1))}
                            >
                                {name}
                            </Button>
                        </span>
                    ))}
                </div>
                <Button variant="ghost" size="icon" onClick={load} disabled={loading}>
                    <RefreshCw className={loading ? "animate-spin h-4 w-4" : "h-4 w-4"} />
                </Button>
            </div>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("model.modelFileName")}</TableHead>
                        <TableHead>{t("model.modelFileSize")}</TableHead>
                        <TableHead>{t("model.modelFileMtime")}</TableHead>
                        <TableHead className="w-[60px]"></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {files.map((file) => (
                        <TableRow key={file.path}>
                            <TableCell>
                                {file.is_dir ? (
                                    <button className="flex items-center gap-1.5 text-left hover:underline" onClick={() => enterDir(file.name)}>
                                        <FolderIcon className="h-4 w-4" />
                                        <span className="font-medium">{file.name}</span>
                                    </button>
                                ) : (
                                    <span className="pl-[22px]">{file.name}</span>
                                )}
                            </TableCell>
                            <TableCell className="text-gray-500">{fmtSize(file.size_bytes)}</TableCell>
                            <TableCell className="text-gray-500">{file.mtime}</TableCell>
                            <TableCell>
                                <Button variant="ghost" size="icon" onClick={() => handleDelete(file)}>
                                    <Trash2 className="h-4 w-4 text-red-500" />
                                </Button>
                            </TableCell>
                        </TableRow>
                    ))}
                    {!files.length && !loading && (
                        <TableRow>
                            <TableCell colSpan={4} className="text-center text-gray-400">{t("model.modelFileEmpty")}</TableCell>
                        </TableRow>
                    )}
                </TableBody>
            </Table>
        </div>
    );
}
