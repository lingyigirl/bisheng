// @ts-strict-ignore
import KnowledgeQa from "./KnowledgeQa";
import { KnowledgeListLayout } from "./KnowledgeListLayout";

/**
 * QA 知识库, as its own menu page.
 *
 * It was a tab under 知识库 until the menu change; the component is the same
 * one, unchanged — what moved is only where it is mounted, so the rail it
 * shares with the other two libraries travels with it.
 */
export default function KnowledgeQaPage() {
    return (
        <KnowledgeListLayout>
            {(deptFilter) => <KnowledgeQa departmentFilter={deptFilter} />}
        </KnowledgeListLayout>
    );
}
