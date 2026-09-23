// @ts-strict-ignore
import TermLibraryTab from "./TermLibraryTab";
import { KnowledgeListLayout } from "./KnowledgeListLayout";

/**
 * 术语库, as its own menu page.
 *
 * It was a tab under 知识库 (F060 put it there so its permission would be the
 * same `knowledge` menu the route already required — page gate and API gate one
 * set, AC-18). It keeps that same `knowledge` permission as a route of its own,
 * so the argument still holds; what changed is only that it is a menu entry
 * rather than a tab.
 */
export default function TermLibraryPage() {
    return (
        <KnowledgeListLayout>
            {(deptFilter) => (
                <TermLibraryTab
                    departmentId={deptFilter.selectedDepartmentId}
                    includeSubDepartments={deptFilter.includeSubDepartments}
                />
            )}
        </KnowledgeListLayout>
    );
}
