// @ts-strict-ignore
import {
    Tabs,
    TabsContent,
    TabsList,
    TabsTrigger,
} from "../../components/bs-ui/tabs";

import { useTranslation } from "react-i18next";
import { cn } from "@/utils";
import { LazyDepartmentTree } from "@/components/bs-comp/department";
import KnowledgeFile from "./KnowledgeFile";
import KnowledgeQa from "./KnowledgeQa";
import { useKnowledgeDepartmentFilter } from "./useKnowledgeDepartmentFilter";


export default function KnowledgePage() {

    const { t } = useTranslation();
    const deptFilter = useKnowledgeDepartmentFilter();

    const defaultValue = (() => {
        const page = window.LibPage;
        return page ? page.type : 'file'
    })();

    // F057: the tree lives at PAGE level, not inside a tab — the file/qa tabs
    // share one organisation filter so switching tabs keeps the scope.
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

            <div className="flex-1 min-w-0 px-2 pt-4 relative">
                {/* The active-filter chip that used to sit here was removed on
                    request: the current scope is already shown by the selected
                    row in the tree on the left, so the extra row above the tabs
                    was noise. */}
                <Tabs defaultValue={defaultValue} className="w-full mb-[40px]">
                    <TabsList className="">
                        <TabsTrigger value="file">{t('lib.fileData')}</TabsTrigger>
                        <TabsTrigger value="qa" className="roundedrounded-xl">{t('lib.qaData')}</TabsTrigger>
                    </TabsList>
                    <TabsContent value="qa">
                        <KnowledgeQa departmentFilter={deptFilter} />
                    </TabsContent>
                    <TabsContent value="file">
                        <KnowledgeFile departmentFilter={deptFilter} />
                    </TabsContent>
                </Tabs>
            </div>
        </div>
    );
}
