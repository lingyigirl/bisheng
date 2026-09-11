import { LoadingIcon } from "@/components/bs-icons/loading";
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/bs-ui/dialog";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { hitTestKnowledgeApi, HitTestResp } from "@/controllers/API";
import { truncateString } from "@/util/utils";
import { SearchCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface HitTestDialogProps {
    knowledgeId: string | number;
    children: React.ReactNode;
}

const QUESTION_MAX_LENGTH = 500;
const CHUNK_DISPLAY_MAX_LENGTH = 120;

/**
 * F055 (AC-14): hit-test dialog — previews the knowledge base's real retrieval
 * for a test question. The backend reuses the same builder as workflow
 * retrieval, so the preview cannot drift from production. Also shows how many
 * files the effective-period filter excluded.
 */
export function HitTestDialog({ knowledgeId, children }: HitTestDialogProps) {
    const { t } = useTranslation('knowledge');
    const [open, setOpen] = useState(false);
    const [question, setQuestion] = useState("");
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<HitTestResp | null>(null);

    const numericKnowledgeId = Number(knowledgeId);

    useEffect(() => {
        if (!open) return;
        setQuestion("");
        setResult(null);
    }, [open, numericKnowledgeId]);

    const handleTest = async () => {
        const trimmed = question.trim();
        if (!trimmed || loading) return;
        setLoading(true);
        const response = await captureAndAlertRequestErrorHoc(
            hitTestKnowledgeApi(numericKnowledgeId, trimmed)
        );
        setLoading(false);
        if (!response || response === 'canceled') return;
        // The request interceptor already unwraps the response envelope —
        // `response` IS the { hits, excluded } payload.
        setResult(response as HitTestResp);
    };

    const excludedTotal = result ? result.excluded.expired + result.excluded.not_yet_effective : 0;

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>{children}</DialogTrigger>
            <DialogContent
                className="w-[640px] max-w-[calc(100vw-32px)] gap-0 rounded-xl border-none p-0"
                onClick={(event) => event.stopPropagation()}
            >
                <DialogHeader className="px-6 py-4">
                    <DialogTitle className="text-[16px] font-medium leading-6 text-[#212121]">
                        {t('hitTest')}
                    </DialogTitle>
                    <span className="text-[12px] leading-5 text-[#86909c]">{t('hitTestDescription')}</span>
                </DialogHeader>
                <div className="flex flex-col gap-4 px-6 pb-5">
                    <div className="flex items-center gap-2">
                        <div className="relative flex h-9 flex-1 items-center rounded-[8px] border border-[#EBECF0] bg-white px-3 transition-colors focus-within:border-primary">
                            <input
                                type="text"
                                value={question}
                                maxLength={QUESTION_MAX_LENGTH}
                                onChange={(event) => setQuestion(event.target.value)}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                        event.preventDefault();
                                        void handleTest();
                                    }
                                }}
                                placeholder={t('hitTestPlaceholder')}
                                className="h-full flex-1 bg-transparent text-sm leading-[22px] text-[#212121] outline-none placeholder-[#86909c]"
                            />
                        </div>
                        <Button
                            className="h-9 gap-1 rounded-[6px] px-4 text-[14px]"
                            onClick={() => void handleTest()}
                            disabled={loading || !question.trim()}
                        >
                            {loading ? <LoadingIcon className="size-4" /> : <SearchCheck size={16} strokeWidth={1.5} />}
                            {t('hitTestRun')}
                        </Button>
                    </div>

                    {result && (
                        <div className="flex flex-col gap-3">
                            {excludedTotal > 0 && (
                                <div className="rounded-[6px] bg-[#fff7e6] px-3 py-2 text-[12px] leading-5 text-[#d46b08]">
                                    {t('hitTestExcluded', {
                                        expired: result.excluded.expired,
                                        notYetEffective: result.excluded.not_yet_effective,
                                    })}
                                </div>
                            )}
                            <div className="text-[14px] font-medium leading-5 text-[#212121]">
                                {t('hitTestResultCount', { count: result.hits.length })}
                            </div>
                            {result.hits.length === 0 && (
                                <span className="py-2 text-[12px] text-[#86909c]">{t('hitTestEmpty')}</span>
                            )}
                            <div className="flex max-h-[360px] flex-col gap-2 overflow-y-auto">
                                {result.hits.map((hit, index) => (
                                    <div
                                        key={`${hit.document_id}-${hit.chunk_index ?? index}`}
                                        className="flex flex-col gap-1 rounded-[6px] border border-[#EBECF0] px-3 py-2"
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <span
                                                className="max-w-[420px] truncate text-[13px] font-medium leading-5 text-[#1d2129]"
                                                title={hit.document_name ?? ''}
                                            >
                                                {hit.document_name ?? '-'}
                                            </span>
                                            <span className="shrink-0 text-[12px] leading-4 text-[#86909c]">
                                                {t('hitTestScoreValue', { score: hit.score.toFixed(4) })}
                                            </span>
                                        </div>
                                        <p
                                            className="text-[13px] leading-5 text-[#4e5969]"
                                            title={hit.chunk}
                                        >
                                            {truncateString(hit.chunk, CHUNK_DISPLAY_MAX_LENGTH)}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
