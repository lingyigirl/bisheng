// @ts-strict-ignore
import { useState } from "react";
import { Button } from "~/components/ui/Button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "~/components";
import DocxPreview from "~/components/PreviewFile/DocxFileViewer";
import { FileIcon, getFileTypebyFileName } from "~/components/ui/icon/File/FileIcon";
import useLocalize from "~/hooks/useLocalize";
import { downloadFile } from "~/utils";

// docx/doc chat attachments open an in-chat online preview (mammoth converts
// the docx to HTML client-side); other file types keep the click-to-download
// behavior. The download button inside the dialog serves the export path.
export function ChatFile({ fileName, filePath }) {
    const t = useLocalize();
    const [previewOpen, setPreviewOpen] = useState(false);

    const type = getFileTypebyFileName(fileName);
    const canPreview = type === 'docx' || type === 'doc';

    // Share links may carry the storage host (or be path-only) — normalize to
    // the workspace origin so the dev/preview proxies resolve them.
    const normalizedPath = filePath
        ? filePath.replace(/https?:\/\/[^\/]+/, __APP_ENV__.BASE_URL)
        : '';

    const handleDownloadFile = () => {
        if (normalizedPath) {
            downloadFile(normalizedPath, fileName);
        }
    };

    const handleClick = () => {
        if (canPreview) {
            setPreviewOpen(true);
            return;
        }
        handleDownloadFile();
    };

    return (
        <>
            <div
                className="group min-w-52 relative flex items-center gap-2 border bg-white p-2 rounded-xl cursor-pointer"
                onClick={handleClick}
            >
                <FileIcon loading={false} type={type} />
                <div className="flex-1">
                    <div className="max-w-48 text-sm font-medium text-gray-700 truncate">
                        {fileName}
                    </div>
                    <p className="text-xs text-gray-400 mt-1">
                        {t(canPreview ? 'com_file_click_to_preview' : 'com_bschoose_click_to_download')}
                    </p>
                </div>
            </div>
            {canPreview && (
                <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
                    <DialogContent className="flex max-h-[85vh] w-[min(90vw,900px)] flex-col gap-3 overflow-hidden">
                        <DialogHeader>
                            <DialogTitle className="truncate text-base">{fileName}</DialogTitle>
                        </DialogHeader>
                        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-white">
                            <DocxPreview filePath={normalizedPath} />
                        </div>
                        <div className="flex justify-end">
                            <Button onClick={handleDownloadFile}>{t('com_file_download')}</Button>
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </>
    );
}
