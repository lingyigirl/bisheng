import { assignKnowledgeDepartmentApi } from "@/controllers/API"
import { toast } from "@/components/bs-ui/toast/use-toast"

/**
 * F057: assign a freshly created library to a department, reporting failures.
 *
 * Returns `true` when the library now belongs to the department, `false` when
 * the assignment was refused — the caller decides what to do about it (the
 * create flow rolls the library back; see KnowledgeFile / KnowledgeQa).
 *
 * Why a refusal is a normal outcome rather than an error to swallow: setting
 * `department_id` is an AUTHORIZATION change. The backend requires
 * `manage_kb_*` on the library, and a non-admin may only target departments in
 * their own subtree. `create_knowledge` (the menu permission that gates the
 * create button) is deliberately a SEPARATE permission — PRD 3.3.3 decouples
 * creation from ReBAC edit rights — so a user can legitimately be allowed to
 * create yet forbidden to place the result under the selected department.
 *
 * The permission cannot be checked up front: `manage_kb_*` is per-library and
 * the library does not exist until after the create call. Hence assign-after-
 * create, and roll back on refusal.
 */
export async function tryAssignDepartment(
    knowledgeId: number,
    departmentId: number,
    deniedMessage: string,
): Promise<boolean> {
    try {
        await assignKnowledgeDepartmentApi(knowledgeId, departmentId)
        return true
    } catch {
        // The reason string (e.g. "outside your scope") is intentionally not
        // surfaced: the toast is user-facing copy, and the backend's English
        // reason codes are not translated.
        toast({ variant: "error", description: deniedMessage })
        return false
    }
}
