import { ScheduledTaskPanel } from "./index"

/**
 * F078: the existing scheduled-task panel is app-type agnostic (it forwards
 * appId/appType to every API). This wrapper pins `appType=100`, the managed-agent
 * sentinel, so the console does not resurrect the old 5/10 assumption.
 */
export function ManagedAgentSchedulePanel({ agentId }: { agentId: string }) {
    return <ScheduledTaskPanel appId={agentId} appType={100} />
}
