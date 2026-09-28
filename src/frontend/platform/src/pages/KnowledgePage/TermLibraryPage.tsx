// @ts-strict-ignore
import TermLibraryTab from "./TermLibraryTab";

/**
 * 术语库, as its own menu page — and global, so it hangs off no department.
 *
 * It was a tab under 知识库 (F060 put it there so its permission would be the
 * same `knowledge` menu the route already required — page gate and API gate one
 * set, AC-18). It keeps that same `knowledge` permission as a route of its own,
 * so that argument still holds.
 *
 * It does NOT use `KnowledgeListLayout`: that component's whole job is the
 * department rail, and the term library is not scoped to one. The file and QA
 * libraries still share it.
 */
export default function TermLibraryPage() {
    return (
        <div className="w-full h-full px-2 pt-4">
            <TermLibraryTab />
        </div>
    );
}
