/**
 * Scenario keywords + related questions under one answer (F059).
 *
 * Two independent halves, each rendered only when non-empty:
 *   - keywords  — terms pulled from this round's question and answer
 *   - questions — follow-up questions drawn from the app's knowledge base
 *
 * Both are ephemeral: they are fetched once when the answer finishes and live in
 * this component's props. Nothing is persisted, so a page reload simply loses
 * them (spec AC-35/AC-43) — there is no history to restore and no "loading
 * skeleton" to leave behind.
 *
 * Clicking either half FILLS THE INPUT — it never sends. Sending on click would
 * make a stray tap cost a model call and an irreversible turn; filling lets the
 * user edit first, which is what they usually want anyway (spec AC-31/AC-40).
 */
import { Outlined } from "bisheng-icons";
import { useLocalize } from "~/hooks";
import { cn } from "~/utils";

interface AnswerSuggestionBarProps {
    keywords?: string[];
    questions?: string[];
    /** Fill the composer with the chosen text. Must not send. */
    onPick: (text: string) => void;
    className?: string;
}

const CHIP =
    "inline-flex items-center rounded-md border border-border-base bg-fill-2 px-2 py-0.5 text-caption text-text-2 transition-colors hover:border-blue-500 hover:text-blue-500";

export function AnswerSuggestionBar({
    keywords = [],
    questions = [],
    onPick,
    className,
}: AnswerSuggestionBarProps) {
    const localize = useLocalize();

    if (keywords.length === 0 && questions.length === 0) return null;

    return (
        <div className={cn("mt-2 space-y-1.5", className)} data-testid="answer-suggestion-bar">
            {keywords.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-caption text-text-3">{localize("com_suggestion_keywords")}</span>
                    {keywords.map((word) => (
                        <button key={word} type="button" className={CHIP} onClick={() => onPick(word)}>
                            {word}
                        </button>
                    ))}
                </div>
            )}

            {questions.length > 0 && (
                <div className="space-y-1">
                    <span className="text-caption text-text-3">{localize("com_suggestion_questions")}</span>
                    <ul className="space-y-0.5">
                        {questions.map((question) => (
                            <li key={question}>
                                <button
                                    type="button"
                                    className="flex w-full items-start gap-1.5 rounded-md px-1 py-0.5 text-left text-body-sm text-text-2 transition-colors hover:bg-fill-2 hover:text-blue-500"
                                    onClick={() => onPick(question)}
                                >
                                    <Outlined.Right className="mt-1 size-3 shrink-0" />
                                    <span>{question}</span>
                                </button>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
