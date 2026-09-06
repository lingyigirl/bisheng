/**
 * F051 — right-side sheet for comparing two workflow versions.
 *
 * Version pickers default per AC-02 (old = the version created just before
 * the one currently loaded in the editor, new = the currently loaded one).
 * The change list groups node-level changes (added / removed / modified) and
 * expands modified nodes into field-level old→new rows; long values are
 * collapsed until expanded (AC-08). Clicking a node row calls
 * ``onLocateNode`` so the parent can center/highlight it on the canvas —
 * this component never touches the editor store (design.md 决策 5/6).
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/bs-ui/badge';
import { Button } from '@/components/bs-ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/bs-ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/bs-ui/sheet';
import {
    compareWorkFlowVersions,
    getWorkFlowVersions,
    type FlowVersionCompareResult,
} from '@/controllers/API/workflow';
import { captureAndAlertRequestErrorHoc } from '@/controllers/request';
import type { FlowVersionItem } from '@/types/flow';
import { cn } from '@/util/utils';

const LONG_VALUE_THRESHOLD = 120;

export interface VersionCompareSheetProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    flowId: string;
    /** Id of the version currently loaded in the editor (TabsContext). */
    currentVersionId: number;
    /** Called when a node change row is clicked — parent locates it on the canvas. */
    onLocateNode: (nodeId: string) => void;
}

type ChangeLabel = 'added' | 'removed' | 'modified';

function changeBadgeClass(change: string): string {
    if (change === 'added') return 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300';
    if (change === 'removed') return 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300';
    return 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300';
}

/** Render an arbitrary diff value as display text. */
function valueToText(value: unknown): string {
    if (value === null || value === undefined) return '∅';
    if (typeof value === 'string') return value;
    return JSON.stringify(value);
}

function ValueBlock({ value, expanded }: { value: unknown; expanded: boolean }) {
    const text = valueToText(value);
    const long = text.length > LONG_VALUE_THRESHOLD;
    const display = long && !expanded ? `${text.slice(0, LONG_VALUE_THRESHOLD)}…` : text;
    return (
        <span className={cn('break-all', long && !expanded && 'text-muted-foreground')}>
            {display || '∅'}
        </span>
    );
}

export function VersionCompareSheet({
    open,
    onOpenChange,
    flowId,
    currentVersionId,
    onLocateNode,
}: VersionCompareSheetProps) {
    const { t } = useTranslation('bs');

    const [versions, setVersions] = useState<FlowVersionItem[]>([]);
    const [fromId, setFromId] = useState<number | null>(null);
    const [toId, setToId] = useState<number | null>(null);
    const [result, setResult] = useState<FlowVersionCompareResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    // On open: reload the version list and apply the AC-02 defaults.
    useEffect(() => {
        if (!open) return;
        setResult(null);
        captureAndAlertRequestErrorHoc(getWorkFlowVersions(flowId)).then((res) => {
            if (!res) return;
            const list = res.data as FlowVersionItem[];
            setVersions(list);
            // Old = the version created just before the currently loaded one.
            const prev = list
                .filter((v) => v.id < currentVersionId)
                .sort((a, b) => b.id - a.id)[0];
            setFromId(prev ? prev.id : null);
            setToId(currentVersionId);
        });
    }, [open, flowId, currentVersionId]);

    const sameVersion = fromId !== null && toId !== null && fromId === toId;
    const canStart = fromId !== null && toId !== null && !sameVersion && !loading;

    const handleStart = useCallback(() => {
        if (fromId === null || toId === null || fromId === toId) return;
        setLoading(true);
        captureAndAlertRequestErrorHoc(
            compareWorkFlowVersions({ flow_id: flowId, from_version_id: fromId, to_version_id: toId }),
        )
            .then((res) => {
                if (res) setResult(res);
            })
            .finally(() => setLoading(false));
    }, [flowId, fromId, toId]);

    const toggleExpand = (key: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(key)) {
                next.delete(key);
            } else {
                next.add(key);
            }
            return next;
        });
    };

    const versionName = (id: number | null): string =>
        versions.find((v) => v.id === id)?.name ?? '';

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            {/* w-1/2 + sm:max-w-none override the base variant's w-3/4 sm:max-w-sm
                (384px cap), so the sheet takes about half the screen instead of a strip. */}
            <SheetContent side="right" className="w-1/2 min-w-[600px] sm:max-w-none overflow-y-auto">
                <SheetHeader className="pb-4">
                    <SheetTitle>{t('skills.compareVersions')}</SheetTitle>
                </SheetHeader>

                {/* Version pickers (AC-02 defaults; AC-03 same-version guard) */}
                <div className="flex items-center gap-3 pb-4">
                    <Select
                        value={fromId === null ? undefined : String(fromId)}
                        onValueChange={(value) => setFromId(Number(value))}
                    >
                        <SelectTrigger className="h-9 w-full">
                            <SelectValue placeholder={t('skills.compareOldVersion')} />
                        </SelectTrigger>
                        <SelectContent>
                            {versions.map((v) => (
                                <SelectItem key={v.id} value={String(v.id)}>
                                    {v.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Select
                        value={toId === null ? undefined : String(toId)}
                        onValueChange={(value) => setToId(Number(value))}
                    >
                        <SelectTrigger className="h-9 w-full">
                            <SelectValue placeholder={t('skills.compareNewVersion')} />
                        </SelectTrigger>
                        <SelectContent>
                            {versions.map((v) => (
                                <SelectItem key={v.id} value={String(v.id)}>
                                    {v.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button
                        type="button"
                        size="sm"
                        className="h-9 shrink-0"
                        disabled={!canStart}
                        onClick={handleStart}
                    >
                        {t('skills.compareStart')}
                    </Button>
                </div>
                {sameVersion && (
                    <p className="pb-2 text-sm text-red-500">{t('skills.compareSelectDifferentVersions')}</p>
                )}

                {/* Result area */}
                {!result && !loading && (
                    <p className="pt-8 text-center text-sm text-muted-foreground">
                        {t('skills.compareOldVersion')} {versionName(fromId)} → {t('skills.compareNewVersion')}{' '}
                        {versionName(toId)}
                    </p>
                )}
                {loading && <p className="pt-8 text-center text-sm text-muted-foreground">…</p>}
                {result && !result.changed && (
                    <p className="pt-8 text-center text-sm text-muted-foreground">
                        {t('skills.compareIdentical')}
                    </p>
                )}
                {result && result.changed && (
                    <div className="space-y-2">
                        {result.node_changes.map((change) => (
                            <NodeChangeRow
                                key={change.node_id}
                                nodeId={change.node_id}
                                name={change.node_name ?? change.node_id}
                                change={change.change}
                                fields={change.fields ?? []}
                                expanded={expanded}
                                labelAdded={t('skills.compareNodeAdded')}
                                labelRemoved={t('skills.compareNodeRemoved')}
                                labelModified={t('skills.compareNodeModified')}
                                labelExpand={t('skills.compareExpand')}
                                labelCollapse={t('skills.compareCollapse')}
                                onLocate={() => onLocateNode(change.node_id)}
                                onToggleExpand={toggleExpand}
                            />
                        ))}
                        {result.edge_changes.map((edge, index) => (
                            <div
                                key={`${edge.source}-${edge.target}-${index}`}
                                className="flex items-center gap-2 rounded-md border bg-white px-3 py-2 text-sm dark:bg-gray-800"
                            >
                                <Badge
                                    variant="secondary"
                                    className={changeBadgeClass(edge.change as ChangeLabel)}
                                >
                                    {edge.change === 'added'
                                        ? t('skills.compareEdgeAdded')
                                        : t('skills.compareEdgeRemoved')}
                                </Badge>
                                <span className="truncate">
                                    {edge.source} → {edge.target}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}

interface NodeChangeRowProps {
    nodeId: string;
    name: string;
    change: string;
    fields: { path: string; old_value: unknown; new_value: unknown }[];
    expanded: Set<string>;
    labelAdded: string;
    labelRemoved: string;
    labelModified: string;
    labelExpand: string;
    labelCollapse: string;
    onLocate: () => void;
    onToggleExpand: (key: string) => void;
}

function NodeChangeRow({
    nodeId,
    name,
    change,
    fields,
    expanded,
    labelAdded,
    labelRemoved,
    labelModified,
    labelExpand,
    labelCollapse,
    onLocate,
    onToggleExpand,
}: NodeChangeRowProps) {
    const label = change === 'added' ? labelAdded : change === 'removed' ? labelRemoved : labelModified;
    return (
        <div className="rounded-md border bg-white dark:bg-gray-800">
            <button
                type="button"
                onClick={onLocate}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
                title={nodeId}
            >
                <Badge variant="secondary" className={changeBadgeClass(change)}>
                    {label}
                </Badge>
                <span className="flex-1 truncate">{name}</span>
            </button>
            {change === 'modified' && fields.length > 0 && (
                <div className="border-t px-3 py-2">
                    {fields.map((field) => {
                        const key = `${nodeId}:${field.path}`;
                        const isExpanded = expanded.has(key);
                        return (
                            <div key={key} className="py-1 text-xs">
                                <p className="truncate text-muted-foreground" title={field.path}>
                                    {field.path}
                                </p>
                                <div className="mt-0.5 flex items-start gap-2">
                                    <span className="min-w-0 flex-1">
                                        <ValueBlock value={field.old_value} expanded={isExpanded} />
                                    </span>
                                    <span className="shrink-0 text-muted-foreground">→</span>
                                    <span className="min-w-0 flex-1">
                                        <ValueBlock value={field.new_value} expanded={isExpanded} />
                                    </span>
                                    {(valueToText(field.old_value).length > LONG_VALUE_THRESHOLD ||
                                        valueToText(field.new_value).length > LONG_VALUE_THRESHOLD) && (
                                        <button
                                            type="button"
                                            className="shrink-0 text-blue-500 hover:underline"
                                            onClick={() => onToggleExpand(key)}
                                        >
                                            {isExpanded ? labelCollapse : labelExpand}
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
