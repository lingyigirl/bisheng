// @ts-strict-ignore
import { LazyDepartmentTree } from "@/components/bs-comp/department";
import { cn } from "@/utils";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useKnowledgeDepartmentFilter } from "./useKnowledgeDepartmentFilter";

/**
 * The department rail shared by every knowledge-library page.
 *
 * It used to live at the top of `KnowledgePage` because the file / QA / term
 * TABS shared one organisation filter (F057), so switching tabs kept the scope.
 * Those three are separate menu pages now, which means each one has to carry
 * the rail itself — hence this, rather than three copies of the same rail that
 * would drift the first time one of them changed.
 *
 * `children` is a render prop because the filter IS the point: every consumer
 * needs the same instance the rail is driving, and taking it from a context
 * would let a page render a rail it is not wired to.
 */
export function KnowledgeListLayout({
    children,
}: {
    children: (deptFilter: ReturnType<typeof useKnowledgeDepartmentFilter>) => ReactNode;
}) {
    const { t } = useTranslation();
    const deptFilter = useKnowledgeDepartmentFilter();

    return (
        <div className="flex w-full h-full">
            <div className="flex w-[240px] min-w-[200px] flex-col border-r p-3">
                {/* "全部" row. The tree only contains real departments, so
                    without this row there is no way back to the unfiltered
                    list — the page auto-selects the user's primary department
                    on load. Rendered here rather than inside LazyDepartmentTree
                    because "no filter" is not a department: it has no dept_id
                    to feed the tree's selection prop, and the tree is shared
                    with three other call sites that must not grow a fake node.
                    Geometry mirrors a depth-0 tree row. */}
                <div
                    className={cn(
                        "flex cursor-pointer items-center rounded-md py-1.5 pl-1.5 pr-2 text-sm hover:bg-accent",
                        deptFilter.selectedDepartmentId == null && "bg-accent font-medium",
                    )}
                    onClick={deptFilter.clearSelection}
                >
                    {t('lib.allDepartments')}
                </div>
                <LazyDepartmentTree
                    controller={deptFilter.tree}
                    selectedDeptId={deptFilter.selectedDepartmentDeptId}
                    onSelect={deptFilter.onSelect}
                    renderRowSuffix={(node) => {
                        const count = deptFilter.counts[node.id];
                        if (!count) return null;
                        return <span className="text-xs text-gray-400">{count}</span>;
                    }}
                />
            </div>

            <div className="flex-1 min-w-0 px-2 pt-4 relative">{children(deptFilter)}</div>
        </div>
    );
}
