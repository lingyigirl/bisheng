import { LoadingIcon } from "@/components/bs-icons/loading";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/bs-ui/table";
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/bs-ui/dialog";
import {
    testDatasourceQueryApi,
    type DatasourceRow,
    type DatasourceTestQueryResult,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface TestQueryDialogProps {
    datasource: DatasourceRow;
    children: React.ReactNode;
}

const QUESTION_MAX_LENGTH = 500;

/**
 * F061 self-test: ask one natural-language question, see the SQL and the rows.
 *
 * Shaped like the knowledge base's HitTestDialog on purpose — "I just
 * configured this, show me it works" is one interaction, not two. It differs
 * in rendering the generated SQL, because for a datasource the statement IS
 * what is being judged: a plausible answer from a wrong query is exactly the
 * failure mode an operator needs to catch here, and a chat window hides it.
 *
 * A failed query is not an error response — the endpoint answers 200 with
 * `degraded: true` and whatever statements the model tried. So the failure
 * banner carries that text rather than an error toast; see the endpoint's
 * docstring for why a 500 would be worse.
 */
export function TestQueryDialog({ datasource, children }: TestQueryDialogProps) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const [question, setQuestion] = useState("");
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<DatasourceTestQueryResult | null>(null);

    useEffect(() => {
        if (!open) return;
        setQuestion("");
        setResult(null);
    }, [open, datasource.id]);

    const handleRun = async () => {
        const trimmed = question.trim();
        if (!trimmed || loading) return;
        setLoading(true);
        const response = await captureAndAlertRequestErrorHoc(
            testDatasourceQueryApi(datasource.id, trimmed)
        );
        setLoading(false);
        if (!response || response === 'canceled') return;
        // The request interceptor already unwraps the envelope — `response` IS
        // the payload.
        setResult(response as DatasourceTestQueryResult);
    };

    const hasRows = !!result && result.row_count > 0 && result.columns.length > 0;

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>{children}</DialogTrigger>
            <DialogContent
                className="w-[720px] max-w-[calc(100vw-32px)] gap-0 rounded-xl border-none p-0"
                onClick={(event) => event.stopPropagation()}
            >
                <DialogHeader className="px-6 py-4">
                    <DialogTitle className="text-[16px] font-medium leading-6 text-[#212121]">
                        {t("datasource.testQuery.title", { name: datasource.name })}
                    </DialogTitle>
                    <span className="text-[12px] leading-5 text-[#86909c]">
                        {t("datasource.testQuery.description")}
                    </span>
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
                                        void handleRun();
                                    }
                                }}
                                placeholder={t("datasource.testQuery.placeholder")}
                                className="h-full flex-1 bg-transparent text-sm leading-[22px] text-[#212121] outline-none placeholder-[#86909c]"
                            />
                        </div>
                        <Button
                            className="h-9 gap-1 rounded-[6px] px-4 text-[14px]"
                            onClick={() => void handleRun()}
                            disabled={loading || !question.trim()}
                        >
                            {loading ? <LoadingIcon className="size-4" /> : <PlayCircle size={16} strokeWidth={1.5} />}
                            {t("datasource.testQuery.run")}
                        </Button>
                    </div>

                    {result && (
                        <div className="flex flex-col gap-3">
                            {result.degraded && (
                                <div className="rounded-[6px] bg-[#fff7e6] px-3 py-2 text-[12px] leading-5 text-[#d46b08]">
                                    {result.answer}
                                </div>
                            )}

                            {/* The statement the model settled on. Absent on a
                                degraded round, where only the attempts exist. */}
                            {result.statement && (
                                <div className="flex flex-col gap-1">
                                    <span className="text-[12px] leading-4 text-[#86909c]">
                                        {t("datasource.testQuery.sqlLabel")}
                                    </span>
                                    <pre className="max-h-[200px] overflow-auto rounded-[6px] bg-[#f7f8fa] p-3 font-mono text-[12px] leading-5 text-[#1d2129]">
                                        {result.statement}
                                    </pre>
                                </div>
                            )}

                            {!result.statement && result.statements_tried.length > 0 && (
                                <div className="flex flex-col gap-1">
                                    <span className="text-[12px] leading-4 text-[#86909c]">
                                        {t("datasource.testQuery.triedLabel")}
                                    </span>
                                    <pre className="max-h-[160px] overflow-auto rounded-[6px] bg-[#f7f8fa] p-3 font-mono text-[12px] leading-5 text-[#4e5969]">
                                        {result.statements_tried.join("\n")}
                                    </pre>
                                </div>
                            )}

                            {result.statement && (
                                <>
                                    <div className="text-[14px] font-medium leading-5 text-[#212121]">
                                        {t("datasource.testQuery.resultCount", { count: result.row_count })}
                                    </div>
                                    {hasRows ? (
                                        <div className="max-h-[320px] overflow-auto rounded-[6px] border border-[#EBECF0]">
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        {result.columns.map((column, index) => (
                                                            <TableHead
                                                                key={`${column}-${index}`}
                                                                className="whitespace-nowrap"
                                                            >
                                                                {column}
                                                            </TableHead>
                                                        ))}
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {result.rows.map((row, rowIndex) => (
                                                        <TableRow key={rowIndex}>
                                                            {row.map((cell, cellIndex) => (
                                                                <TableCell
                                                                    key={cellIndex}
                                                                    className="whitespace-nowrap text-[13px]"
                                                                >
                                                                    {cell === null || cell === undefined ? '' : String(cell)}
                                                                </TableCell>
                                                            ))}
                                                        </TableRow>
                                                    ))}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    ) : (
                                        <span className="py-2 text-[12px] text-[#86909c]">
                                            {t("datasource.testQuery.empty")}
                                        </span>
                                    )}
                                    {result.truncated && (
                                        <span className="text-[12px] leading-4 text-[#86909c]">
                                            {t("datasource.testQuery.truncated")}
                                        </span>
                                    )}
                                </>
                            )}
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
