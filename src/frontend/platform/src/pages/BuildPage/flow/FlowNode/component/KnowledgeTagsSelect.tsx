import { Label } from "@/components/bs-ui/label";
import { QuestionTooltip } from "@/components/bs-ui/tooltip";
import { getKnowledgeTagsApi, KnowledgeTagItem } from "@/controllers/API/knowledgeTags";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { truncateString } from "@/util/utils";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface KnowledgeTagsSelectProps {
    data: { key?: string; value: number[]; label?: boolean; help?: boolean };
    onChange: (value: number[]) => void;
    i18nPrefix: string;
    selectedKnowledgeIds: () => string[];
}

const TAG_DISPLAY_MAX_LENGTH = 14;

/**
 * F055 (AC-03): tag filter for the retrieval node — only documents carrying the
 * selected tags are recalled. Tags are KB-scoped, so the list merges the tags
 * of every selected knowledge base and the backend resolves them per KB
 * (design pitfall 9). Reloads when the KB selection changes.
 */
export default function KnowledgeTagsSelect({
    data,
    onChange,
    i18nPrefix,
    selectedKnowledgeIds,
}: KnowledgeTagsSelectProps) {
    const { t } = useTranslation('flow');
    const [tags, setTags] = useState<KnowledgeTagItem[]>([]);

    // selectedKnowledgeIds is a fresh closure per render by design (the flow
    // canvas re-creates it) — close over the derived string key instead.
    const kbIdsKey = (selectedKnowledgeIds?.() || []).join(',');

    const loadTags = useCallback(async () => {
        const kbIds = kbIdsKey.split(',').map(Number).filter(Boolean);
        if (!kbIds.length) {
            setTags([]);
            return;
        }
        const lists = await Promise.all(
            kbIds.map((id) => captureAndAlertRequestErrorHoc(getKnowledgeTagsApi(id, { page: 1, limit: 100 })))
        );
        const seen = new Set<number>();
        const merged: KnowledgeTagItem[] = [];
        for (const res of lists) {
            if (!res || res === 'canceled') continue;
            for (const tag of (res as { data: KnowledgeTagItem[] }).data ?? []) {
                if (seen.has(tag.id)) continue;
                seen.add(tag.id);
                merged.push(tag);
            }
        }
        setTags(merged);
    }, [kbIdsKey]);

    useEffect(() => {
        void loadTags();
    }, [loadTags]);

    const toggleTag = (tagId: number) => {
        const current = Array.isArray(data.value) ? data.value : [];
        const next = current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId];
        onChange(next);
    };

    return (
        <div className="node-item mb-4" data-key={data.key}>
            <Label className="flex items-center bisheng-label">
                {data.label && t(`${i18nPrefix}label`)}
                {data.help && <QuestionTooltip content={t(`${i18nPrefix}help`)} />}
            </Label>
            <div className="nodrag mt-2 flex flex-wrap gap-1.5">
                {tags.length === 0 && (
                    <span className="text-xs text-gray-400">{t('retrievalTagsEmpty')}</span>
                )}
                {tags.map((tag) => {
                    const isSelected = (data.value ?? []).includes(tag.id);
                    return (
                        <span
                            key={tag.id}
                            onClick={() => toggleTag(tag.id)}
                            className={`flex h-6 cursor-pointer items-center justify-center rounded-[4px] px-2 text-xs leading-5 transition-colors ${isSelected
                                ? 'bg-blue-100 text-blue-600'
                                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                                }`}
                            title={tag.name}
                        >
                            {truncateString(tag.name, TAG_DISPLAY_MAX_LENGTH)}
                        </span>
                    );
                })}
            </div>
        </div>
    );
}
