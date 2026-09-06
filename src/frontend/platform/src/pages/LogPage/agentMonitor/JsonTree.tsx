import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

// Lightweight collapsible JSON viewer for node input/output payloads (F052).
// Self-written on purpose: no new UI libraries are allowed in the platform.

type JsonLeaf = string | number | boolean | null;
type JsonValue = JsonLeaf | JsonValue[] | { [key: string]: JsonValue };

interface JsonTreeProps {
    data: unknown;
    /** Depth below which nodes start collapsed (root is depth 0) */
    defaultExpandDepth?: number;
    emptyText?: string;
}

interface JsonNodeProps {
    name: string | number;
    value: JsonValue;
    depth: number;
    defaultExpandDepth: number;
}

const isObject = (v: JsonValue): v is { [key: string]: JsonValue } =>
    typeof v === "object" && v !== null && !Array.isArray(v);

function leafText(value: JsonLeaf): string {
    if (value === null) return "null";
    if (typeof value === "string") return JSON.stringify(value.length > 120 ? `${value.slice(0, 120)}…` : value);
    return String(value);
}

function leafClass(value: JsonLeaf): string {
    if (value === null) return "text-gray-400 italic";
    if (typeof value === "string") return "text-emerald-700 dark:text-emerald-400";
    if (typeof value === "number") return "text-blue-700 dark:text-blue-400";
    return "text-purple-700 dark:text-purple-400";
}

function JsonNode({ name, value, depth, defaultExpandDepth }: JsonNodeProps) {
    const [open, setOpen] = useState(depth < defaultExpandDepth);
    const isLeaf = value === null || typeof value !== "object";

    if (isLeaf) {
        return (
            <div className="flex gap-1 font-mono text-xs leading-5">
                <span className="text-gray-500">{name}:</span>
                <span className={leafClass(value)}>{leafText(value)}</span>
            </div>
        );
    }

    const isArray = Array.isArray(value);
    const entries: [string | number, JsonValue][] = isArray
        ? value.map((v, i) => [i, v] as [number, JsonValue])
        : Object.entries(value);

    return (
        <div>
            <button
                type="button"
                onClick={() => setOpen(!open)}
                className="flex items-center gap-1 font-mono text-xs text-gray-700 dark:text-gray-300 hover:text-blue-600"
            >
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                <span className="text-gray-500">{name}:</span>
                <span>{isArray ? `[${entries.length} items]` : `{${entries.length} keys}`}</span>
            </button>
            {open && (
                <div className="ml-4 border-l border-gray-200 dark:border-gray-700 pl-3">
                    {entries.length === 0 && <div className="text-xs text-gray-400">{isArray ? "[]" : "{}"}</div>}
                    {entries.map(([k, v]) => (
                        <JsonNode
                            key={String(k)}
                            name={k}
                            value={v}
                            depth={depth + 1}
                            defaultExpandDepth={defaultExpandDepth}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

export function JsonTree({ data, defaultExpandDepth = 1, emptyText = "-" }: JsonTreeProps) {
    const normalized = (data ?? null) as JsonValue;
    const isEmpty = data === null || data === undefined || (typeof data === "object" && Object.keys(data).length === 0);
    if (isEmpty) return <span className="text-xs text-gray-400">{emptyText}</span>;

    return (
        <div className="max-h-72 overflow-y-auto rounded-md bg-gray-50 p-2 dark:bg-zinc-900">
            {isObject(normalized) || Array.isArray(normalized) ? (
                <JsonNode name="root" value={normalized} depth={0} defaultExpandDepth={defaultExpandDepth} />
            ) : (
                <div className="flex gap-1 font-mono text-xs">
                    <span className={leafClass(normalized)}>{leafText(normalized)}</span>
                </div>
            )}
        </div>
    );
}
