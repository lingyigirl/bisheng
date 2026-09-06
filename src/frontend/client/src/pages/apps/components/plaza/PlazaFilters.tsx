// @ts-strict-ignore
"use client"

import { useCallback } from "react"
import { useLocalize } from "~/hooks"
import { Button } from "~/components/ui/Button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "~/components/ui"
import { cn } from "~/utils"
import { ChevronDown } from "lucide-react"

export const FLOW_TYPE_ALL = null
export const FLOW_TYPE_ASSISTANT = 5
export const FLOW_TYPE_WORKFLOW = 10

export type PlazaSortBy = "recent" | "update_time" | "visit_count"

export interface PlazaFilterState {
    flowType: number | null
    tagIds: number[]
    sortBy: PlazaSortBy
}

interface PlazaFiltersProps {
    value: PlazaFilterState
    tags: { id: number; name: string }[]
    onChange: (patch: Partial<PlazaFilterState>) => void
}

const TYPE_OPTIONS: { key: string; value: number | null }[] = [
    { key: "com_app_plaza_type_all", value: FLOW_TYPE_ALL },
    { key: "com_app_plaza_type_assistant", value: FLOW_TYPE_ASSISTANT },
    { key: "com_app_plaza_type_workflow", value: FLOW_TYPE_WORKFLOW },
]

const SORT_OPTIONS: { key: string; value: PlazaSortBy }[] = [
    { key: "com_app_plaza_sort_recent", value: "recent" },
    { key: "com_app_plaza_sort_newest", value: "update_time" },
    { key: "com_app_plaza_sort_visits", value: "visit_count" },
]

export function PlazaFilters({ value, tags, onChange }: PlazaFiltersProps) {
    const localize = useLocalize()

    const toggleTag = useCallback(
        (tagId: number) => {
            const has = value.tagIds.includes(tagId)
            onChange({
                tagIds: has ? value.tagIds.filter((id) => id !== tagId) : [...value.tagIds, tagId],
            })
        },
        [value.tagIds, onChange],
    )

    const sortLabel = SORT_OPTIONS.find((o) => o.value === value.sortBy)?.key ?? "com_app_plaza_sort_recent"

    return (
        <div className="flex w-full flex-col gap-2">
            {/* Row 1: app type chips + sort */}
            <div className="flex items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    {TYPE_OPTIONS.map((opt) => (
                        <Button
                            key={opt.key}
                            variant="outline"
                            size="sm"
                            className={cn(
                                "h-8 rounded-md px-3 text-body-sm",
                                value.flowType === opt.value && "border-blue-500 bg-blue-500/[0.07] text-blue-500",
                            )}
                            onClick={() => onChange({ flowType: opt.value })}
                        >
                            {localize(opt.key)}
                        </Button>
                    ))}
                </div>
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm" className="h-8 gap-1 rounded-md px-2 text-body-sm text-text-3">
                            {localize(sortLabel)}
                            <ChevronDown className="size-3.5" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                        {SORT_OPTIONS.map((opt) => (
                            <DropdownMenuItem
                                key={opt.value}
                                onSelect={() => onChange({ sortBy: opt.value })}
                                className={cn(value.sortBy === opt.value && "text-blue-500")}
                            >
                                {localize(opt.key)}
                            </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
            {/* Row 2: tag multi-select chips (OR semantics) */}
            {tags.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                    {tags.map((tag) => {
                        const active = value.tagIds.includes(tag.id)
                        return (
                            <Button
                                key={tag.id}
                                variant="outline"
                                size="sm"
                                className={cn(
                                    "h-7 rounded-md px-2.5 text-caption",
                                    active && "border-blue-500 bg-blue-500/[0.07] text-blue-500",
                                )}
                                onClick={() => toggleTag(tag.id)}
                            >
                                {tag.name}
                            </Button>
                        )
                    })}
                </div>
            )}
        </div>
    )
}
