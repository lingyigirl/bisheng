// Document-level Markdown renderer shared by the F035 skill sheet and the F076
// access guide. The typography tokens are the ones the skill sheet introduced
// (20px h1, 14px/1.7 body, dark `pre`) so both surfaces stay visually
// identical; on top of that it adds what a handbook needs — tables scroll
// horizontally instead of overflowing a narrow panel, and code blocks can
// carry a copy button.
//
// i18n stays out of this file: copy labels arrive via props (the same contract
// the shared UI package follows), and the button is a SIBLING of the <pre>,
// never a child — a child would leak its own label into the copied text
// through textContent.
import { toast } from "@/components/bs-ui/toast/use-toast"
import { copyText } from "@/utils"
import { Check, Copy } from "lucide-react"
import * as React from "react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

/** Labels for the per-code-block copy button. */
export interface CodeCopyLabels {
    copy: string
    copied: string
    failed: string
}

interface MarkdownDocProps {
    markdown: string
    className?: string
    /** Omit to render code blocks without a copy button (the skill sheet's default look). */
    codeCopy?: CodeCopyLabels
}

const COPIED_RESET_MS = 2000

const PRE_CLASS =
    "bg-[#1F2430] text-[#E6EAF2] rounded-[10px] p-3.5 overflow-x-auto text-[12.5px] leading-relaxed [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit"

const DOC_COMPONENTS: Components = {
    h1: ({ children }) => <h1 className="text-[20px] leading-[1.35] font-bold tracking-tight text-[#161B26] dark:text-gray-100 mt-0 mb-3 break-words">{children}</h1>,
    h2: ({ children }) => <h2 className="text-[15.5px] leading-snug font-semibold text-[#1F2430] dark:text-gray-100 mt-[18px] mb-2 break-words">{children}</h2>,
    h3: ({ children }) => <h3 className="text-[14px] leading-snug font-semibold text-[#1F2430] dark:text-gray-100 mt-3.5 mb-1.5 break-words">{children}</h3>,
    p: ({ children }) => <p className="text-[14px] leading-[1.7] text-[#3F4654] dark:text-gray-300 my-0 mb-3 break-words">{children}</p>,
    ul: ({ children }) => <ul className="list-disc pl-5 my-2.5 space-y-1.5 text-[14px] leading-[1.7] text-[#3F4654] dark:text-gray-300">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal pl-5 my-2.5 space-y-1.5 text-[14px] leading-[1.7] text-[#3F4654] dark:text-gray-300">{children}</ol>,
    li: ({ children }) => <li className="leading-[1.7] pl-1 break-words">{children}</li>,
    a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2 break-words">{children}</a>,
    strong: ({ children }) => <strong className="font-semibold text-[#1F2430] dark:text-gray-100">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    code: ({ children }) => <code className="px-1.5 py-0.5 rounded bg-muted text-[13px] font-mono text-[#1F2430] dark:text-gray-200">{children}</code>,
    blockquote: ({ children }) => <blockquote className="border-l-2 border-border pl-3 text-muted-foreground my-3">{children}</blockquote>,
    hr: () => <hr className="my-4 border-border" />,
    // Wide tables (e.g. the 21-row open-API error-code table) scroll inside
    // their own container instead of stretching the surrounding panel.
    table: ({ children }) => (
        <div className="my-3 overflow-x-auto">
            <table className="w-full min-w-[420px] border-collapse text-[13px]">{children}</table>
        </div>
    ),
    th: ({ children }) => <th className="border border-border px-2.5 py-1.5 bg-muted text-left font-medium">{children}</th>,
    td: ({ children }) => <td className="border border-border px-2.5 py-1.5">{children}</td>,
}

/**
 * A fenced code block with its copy button. The button sits next to the <pre>
 * (absolute, same relative wrapper), so `pre.textContent` is exactly the code.
 */
function CodeBlock({ labels, children }: { labels: CodeCopyLabels; children?: React.ReactNode }) {
    const preRef = React.useRef<HTMLPreElement>(null)
    const [copied, setCopied] = React.useState(false)
    const resetTimer = React.useRef<number | undefined>(undefined)

    React.useEffect(() => () => window.clearTimeout(resetTimer.current), [])

    const handleCopy = () => {
        const text = preRef.current?.textContent ?? ""
        copyText(text)
            .then(() => {
                setCopied(true)
                window.clearTimeout(resetTimer.current)
                resetTimer.current = window.setTimeout(() => setCopied(false), COPIED_RESET_MS)
            })
            // copyText does not guard writeText rejections itself; without this
            // catch a denied clipboard would surface as an unhandled rejection.
            .catch(() => toast({ variant: "warning", description: labels.failed }))
    }

    return (
        <div className="relative my-3">
            <pre ref={preRef} className={PRE_CLASS}>{children}</pre>
            <button
                type="button"
                onClick={handleCopy}
                title={labels.copy}
                className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/10 px-2 py-1 text-[11.5px] text-[#C9D2E3] transition-colors hover:bg-white/20 hover:text-white"
            >
                {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
                <span>{copied ? labels.copied : labels.copy}</span>
            </button>
        </div>
    )
}

/** Render a full Markdown document with the platform's document typography. */
export function MarkdownDoc({ markdown, className, codeCopy }: MarkdownDocProps) {
    const components = React.useMemo<Components>(() => {
        if (!codeCopy) return DOC_COMPONENTS
        return {
            ...DOC_COMPONENTS,
            pre: ({ children }) => <CodeBlock labels={codeCopy}>{children}</CodeBlock>,
        }
    }, [codeCopy])

    return (
        <div className={className}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{markdown}</ReactMarkdown>
        </div>
    )
}
