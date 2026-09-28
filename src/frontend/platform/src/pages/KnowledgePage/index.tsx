// @ts-strict-ignore
import KnowledgeFile from "./KnowledgeFile";
import { KnowledgeListLayout } from "./KnowledgeListLayout";

/**
 * 文档知识库 — the file library, and (since the menu change) only that.
 *
 * The file / QA / term TABS that used to live here are gone: QA and the term
 * library are their own menu entries now, so this page carries one library and
 * the tab machinery around a single tab would be noise. The department rail is
 * shared with the other two pages — see `KnowledgeListLayout`, which also
 * records why the rail moved.
 */
export default function KnowledgePage() {
    return (
        <KnowledgeListLayout>
            {(deptFilter) => <KnowledgeFile departmentFilter={deptFilter} />}
        </KnowledgeListLayout>
    );
}
