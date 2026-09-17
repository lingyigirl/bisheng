/**
 * The "AI-generated content" line shown under every answer (F059).
 *
 * Deliberately NOT configurable and NOT tied to `allowFeedback`: the share/read-only
 * views hide the rating buttons and the suggestion bar, but this line stays. A
 * shared answer is still AI-generated content reaching someone outside the
 * conversation, and that is exactly the case the disclaimer exists for.
 *
 * It renders from i18n only — no server-side copy, no per-tenant override (spec AC-48).
 */
import { useLocalize } from "~/hooks";

interface AiDisclaimerProps {
    className?: string;
}

export function AiDisclaimer({ className }: AiDisclaimerProps) {
    const localize = useLocalize();

    return (
        <p className={className ?? "mt-1 text-caption text-text-3"} data-testid="ai-disclaimer">
            {localize("com_ai_generated_disclaimer")}
        </p>
    );
}
