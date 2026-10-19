import { mergeTextOutput } from '~/hooks/Websocket/textOutput';
/**
 * Step-tree construction: merge raw WS frames into steps, then group them into the
 * renderable node trees (flow nodes / timeline nodes). Split out of stepUtils.ts —
 * see execTypes.ts for the frame contract this consumes.
 */
import type {
    DeepStepGroup,
    ExecStepEventData,
    FlowNode,
    MergedStep,
    SubagentAgent,
    SubagentGroup,
    SubagentSegment,
    TimelineNode,
} from './execTypes';

/**
 * Explode a namespace-grouped subagent team into one flat top-level segment per
 * subagent (R3 完全拆平). Goals are matched to agents BY ORDER and only when the
 * burst's goal count equals the agent count — the backend does not bind a goal to
 * a specific subgraph namespace (see stream_event_mapper `_handle_tool_starts`),
 * so a count mismatch degrades to no goal (the renderer falls back to "子智能体 N").
 */
export function explodeSubagentGroup(group: SubagentGroup): SubagentSegment[] {
    const aligned = (group.goals?.length ?? 0) === group.agents.length;
    return group.agents.map((agent, i) => {
        const steps = [agent.step, ...agent.children];
        let startedAt: number | undefined;
        let endedAt: number | undefined;
        let running = false;
        for (const s of steps) {
            if (s.startedAt !== undefined) {
                startedAt = startedAt === undefined ? s.startedAt : Math.min(startedAt, s.startedAt);
            }
            if (s.endedAt !== undefined) {
                endedAt = endedAt === undefined ? s.endedAt : Math.max(endedAt, s.endedAt);
            }
            if (s.running) running = true;
        }
        return {
            kind: 'subagent_segment' as const,
            goal: aligned ? group.goals![i] || '' : '',
            idx: agent.idx ?? i + 1,
            steps,
            startedAt,
            endedAt,
            running,
        };
    });
}

/** Merge raw start/end frames by call_id, preserving first-seen order. */
export function mergeStepFrames(history: ExecStepEventData[] | null | undefined): MergedStep[] {
    const byId = new Map<string, MergedStep>();
    const order: string[] = [];

    (history || []).forEach((frame, idx) => {
        if (!frame) return;
        // `call_user_input` is NOT dropped here anymore (时序内联 2026-06): it now
        // rides the merged stream as a SEGMENT BOUNDARY so the answered clarify
        // renders as an inline IntentRow at its chronological position (cutting the
        // pre-question thinking from the resumed thinking) instead of being hoisted
        // to the panel top. buildTimelineGroups consumes it: an answered one emits
        // an `intent` node; an unanswered (parked) one only flushes the open episode
        // and is otherwise dropped (rendered as the active ClarifyCard via
        // findPendingUserInput). The frame carries no call_id/status/name (it is a
        // NeedUserInput model_dump, not an ExecStep), so it lands on `__step_${idx}`.
        // `ask_user` is the HITL interrupt mechanism, surfaced as a ClarifyCard /
        // IntentRow — not a normal tool step. Its tool-call frame emits a `start`
        // but never an `end` (interrupt() halts the graph), so rendering it as a
        // ToolRow would spin forever. Drop it.
        // `ls` is the agent's internal workspace exploration (typically empty at
        // the start of a no-upload task) — display noise, not a deliverable step.
        // `write_todos` is NOT dropped here (段流重构 2026-06): after the B2
        // single-bucket change it is the SEGMENT BOUNDARY that cuts the one
        // execution stream into episodes. Its frame must survive merge so
        // buildTimelineGroups can flush on it; it is still never rendered as a row
        // and classifyActivity returns null for it, so it pollutes neither the
        // step list nor the activity summary. buildFlowNodes drops only the
        // namespaced (subagent-internal) ones.
        if (frame.name === 'ask_user' || frame.name === 'ls') return;
        const callId = frame.call_id || `__step_${idx}`;
        const ts = typeof frame.timestamp === 'number' ? frame.timestamp : undefined;
        const existing = byId.get(callId);
        if (!existing) {
            byId.set(callId, {
                callId,
                taskId: frame.task_id || '',
                name: frame.name || '',
                stepType: frame.step_type || 'tool',
                running: frame.status !== 'end',
                callReason: frame.call_reason || '',
                params: frame.params || null,
                output: frame.output || '',
                // The backend ships the subgraph namespace nested in
                // extra_info.namespace (ExecStep has no top-level `namespace`
                // field — see stream_event_mapper). Read it from there; keep the
                // top-level `frame.namespace` as a legacy fallback for fixtures.
                namespace: frame.extra_info?.namespace ?? frame.namespace ?? null,
                extraInfo: frame.extra_info || {},
                // first-seen frame timestamp starts the clock; end/later frames
                // extend endedAt below
                startedAt: typeof frame.started_at === 'number' ? frame.started_at : ts,
                endedAt: ts,
                raw: frame,
            });
            order.push(callId);
            return;
        }
        // later frame for the same call: end frame closes the step; outputs of
        // streaming (thinking) frames are appended defensively
        if (frame.status === 'end') existing.running = false;
        if (frame.params && Object.keys(frame.params).length) existing.params = frame.params;
        if (frame.step_type === 'assistant') {
            existing.output = mergeTextOutput(existing.output, frame);
        } else if (frame.output) {
            existing.output =
                existing.output && frame.output !== existing.output
                    ? existing.output + frame.output
                    : frame.output;
        }
        if (frame.extra_info) existing.extraInfo = { ...existing.extraInfo, ...frame.extra_info };
        if (ts !== undefined) {
            if (existing.startedAt === undefined) existing.startedAt = ts;
            existing.endedAt = ts;
        }
        existing.raw = frame;
    });

    return order.map((id) => byId.get(id)!);
}

/**
 * True when two MergedStep snapshots render IDENTICALLY — the single "did this step
 * change?" predicate behind the timeline's React.memo gates (DeepStepGroup's
 * per-step loop + ToolRowLite). Centralized here so the two memo comparators can't
 * drift apart as the render path gains a field.
 *
 * The WS pump rebuilds fresh MergedStep objects every frame (see mergeStepFrames),
 * so the comparators must work on values, not identity — yet two reference checks
 * are sound: `params` traces back to the stable raw history frame (same object for
 * an unchanged step), and streaming `output` only ever grows so its length is a
 * reliable change signal. Compare only the rendered job status from `extraInfo`:
 * mergeStepFrames spreads a new metadata object on each rebuild. The terminal
 * timestamp also participates so a corrected replay cannot retain a stale clock.
 */
export function mergedStepRenderEqual(a: MergedStep, b: MergedStep): boolean {
    return (
        a.callId === b.callId &&
        a.running === b.running &&
        a.name === b.name &&
        a.stepType === b.stepType &&
        (a.stepType === 'assistant' ? a.output === b.output : a.output.length === b.output.length) &&
        a.params === b.params &&
        a.extraInfo.job_status === b.extraInfo.job_status &&
        a.endedAt === b.endedAt &&
        a.callReason === b.callReason
    );
}

/**
 * (C) Merge consecutive thinking steps that share the same namespace (null==null
 * counts as same) into one rendered thinking step. The backend persists thinking
 * as many tiny token-delta frames (technical debt; see §7 open decision 1) — the
 * render layer stitches the adjacent ones back into a single passage:
 * - output concatenated SEAMLESSLY ("") — each delta already carries its own
 *   leading space and the model's own newlines, so a "\n\n" separator would
 *   shatter one continuous reasoning into a blank-line-per-token "poem".
 * - startedAt = earliest, endedAt = latest, running = last item's running
 * - callId taken from the first item (stable react key)
 * Thinking across different namespaces is NOT merged (avoid cross-subagent
 * contamination). Non-thinking steps pass through untouched and break a run.
 */
export function mergeAdjacentThinking(steps: MergedStep[]): MergedStep[] {
    const out: MergedStep[] = [];
    for (const step of steps) {
        const prev = out[out.length - 1];
        if (
            prev &&
            step.stepType === 'thinking' &&
            prev.stepType === 'thinking' &&
            prev.namespace === step.namespace
        ) {
            // fold into prev — clone first so we never mutate the input array
            const merged: MergedStep = out[out.length - 1] === prev ? { ...prev } : prev;
            merged.output = [merged.output, step.output].filter(Boolean).join('');
            if (step.startedAt !== undefined) {
                merged.startedAt =
                    merged.startedAt === undefined ? step.startedAt : Math.min(merged.startedAt, step.startedAt);
            }
            if (step.endedAt !== undefined) {
                merged.endedAt =
                    merged.endedAt === undefined ? step.endedAt : Math.max(merged.endedAt, step.endedAt);
            }
            merged.running = step.running;
            merged.raw = step.raw;
            out[out.length - 1] = merged;
            continue;
        }
        out.push(step);
    }
    return out;
}

/** Group explicit invocations and legacy namespaces independently.
 * Background members may interleave with main work. A namespace without its
 * delegation marker remains a separate speaker; it never becomes main prose.
 */
export function buildFlowNodes(steps: MergedStep[]): FlowNode[] {
    const merged = mergeAdjacentThinking(steps);
    const nodes: FlowNode[] = [];
    const invocationGroups = new Map<string, SubagentGroup>();
    // ns -> agent bucket; persists for the whole task so a subagent's later steps
    // always find their agent, even across an intervening top-level step.
    const agentByNamespace = new Map<string, SubagentAgent>();
    let memberIndex = 0;
    // Legacy metadata is retained for old consumers. Speaker identity comes from
    // the namespace; arrival order cannot reliably bind a goal to an agent.
    let currentGroup: SubagentGroup | null = null;
    let pendingGoals: string[] = [];
    let pendingName = '';

    for (const step of merged) {
        const invocation = step.extraInfo.invocation_id;
        if (typeof invocation === 'string' && invocation) {
            let group = invocationGroups.get(invocation);
            if (!group) {
                group = { kind: 'subagent_group', name: String(step.extraInfo.agent_name || step.name),
                    agents: [{ step, children: [], idx: ++memberIndex }], goals: [String(step.extraInfo.delegate_goal || '')] };
                invocationGroups.set(invocation, group);
                nodes.push(group);
            } else if (step.callId === invocation) {
                if (group.agents[0].step.callId !== invocation) group.agents[0].children.unshift(group.agents[0].step);
                group.agents[0].step = step;
            } else group.agents[0].children.push(step);
            continue;
        }
        // write_todos is the plan-write call. A subagent-internal one (namespaced)
        // is noise — drop it so it never becomes a subagent anchor or a row. The
        // main-graph one (ns is None) falls through to the top-level branch below,
        // where it is pushed as a step node and consumed as a segment boundary by
        // buildTimelineGroups (段流重构 2026-06).
        if (step.name === 'write_todos' && step.namespace) continue;

        // main-graph delegation point (B2): record goal/name only — no node yet.
        if (step.stepType === 'subagent') {
            const goal = step.callReason || step.extraInfo?.delegate_goal || '';
            if (currentGroup) {
                if (step.name) currentGroup.name = step.name;
                if (goal && !currentGroup.goals!.includes(goal)) currentGroup.goals!.push(goal);
            } else {
                if (step.name) pendingName = step.name;
                if (goal && !pendingGoals.includes(goal)) pendingGoals.push(goal);
            }
            continue;
        }

        // subagent-internal step: bucket by distinct namespace.
        if (step.namespace) {
            const known = agentByNamespace.get(step.namespace);
            if (known) {
                known.children.push(step);
                continue;
            }
            // A background member may first appear after intervening main work.
            if (step.namespace) {
                if (!currentGroup) {
                    currentGroup = {
                        kind: 'subagent_group',
                        name: pendingName || step.name,
                        agents: [],
                        goals: pendingGoals,
                    };
                    nodes.push(currentGroup);
                    pendingGoals = [];
                    pendingName = '';
                }
                const agent: SubagentAgent = { step, children: [], idx: ++memberIndex };
                currentGroup.agents.push(agent);
                agentByNamespace.set(step.namespace, agent);
                continue;
            }
        }

        // Main work closes only the current legacy burst. Known namespaces stay
        // bound across this boundary while background members continue.
        nodes.push({ kind: 'step', step });
        currentGroup = null;
        pendingGoals = [];
        pendingName = '';
    }

    return nodes;
}

/**
 * (Wave2 / F2b) Aggregate the flow nodes ONE level higher, so the task-mode
 * timeline reads like the daily-chat "deep thinking" surface:
 * - run buildFlowNodes (keeps the 22→3 subagent_group grouping untouched);
 * - wrap each maximal run of consecutive `{ kind: 'step' }` nodes into a single
 *   `deep_step_group` (thinking + tool + knowledge in one collapsible episode);
 * - a `subagent_group` breaks the run and passes through verbatim.
 *
 * A lone top-level step is ALSO wrapped in a deep_step_group (uniform rendering
 * — ExecutionTimeline only has to dispatch two node kinds). buildTimelineGroups
 * accepts ANY MergedStep[], so the same primitive serves any pure thinking+tool
 * run (no delegation frame, no namespace flip) that collapses to deep_step_groups.
 */
/**
 * (段流重构 2026-06) write_todos is the SEGMENT BOUNDARY. After the B2
 * single-bucket change the whole main-graph execution lands in ONE ordered
 * stream, and each main-graph write_todos call cuts it into an episode ("段").
 * The boundary frame itself is never rendered (the plan is owned by the bottom
 * TaskPanel) — it only flushes the open episode.
 */
function isSegmentBoundary(step: MergedStep): boolean {
    return step.name === 'write_todos';
}

export function buildTimelineGroups(steps: MergedStep[]): TimelineNode[] {
    const flow = buildFlowNodes(steps);
    const out: TimelineNode[] = [];
    // open episode being accumulated; flushed when a write_todos segment boundary
    // or a subagent_group breaks the run, or the input ends.
    let episode: MergedStep[] = [];

    const flush = () => {
        if (!episode.length) return;
        let startedAt: number | undefined;
        let endedAt: number | undefined;
        let running = false;
        for (const s of episode) {
            if (s.startedAt !== undefined) {
                startedAt = startedAt === undefined ? s.startedAt : Math.min(startedAt, s.startedAt);
            }
            if (s.endedAt !== undefined) {
                endedAt = endedAt === undefined ? s.endedAt : Math.max(endedAt, s.endedAt);
            }
            if (s.running) running = true;
        }
        out.push({ kind: 'deep_step_group', steps: episode, startedAt, endedAt, running });
        episode = [];
    };

    for (const node of flow) {
        if (node.kind === 'step') {
            // call_user_input is a SEGMENT BOUNDARY (时序内联 2026-06): it closes the
            // pre-question episode so the resumed thinking starts a fresh one. An
            // ANSWERED clarify emits an inline `intent` node (IntentRow) at this
            // position; an UNANSWERED (parked) one is dropped here — it surfaces as
            // the active ClarifyCard via findPendingUserInput, not in the timeline.
            if (node.step.stepType === 'call_user_input') {
                flush();
                if (node.step.raw?.is_completed) {
                    out.push({ kind: 'intent', data: node.step.raw, startedAt: node.step.startedAt });
                }
                continue;
            }
            // write_todos cuts a segment boundary: flush the open episode but
            // never render the marker itself (段流重构 2026-06).
            if (isSegmentBoundary(node.step)) {
                flush();
                continue;
            }
            if (node.step.stepType === 'assistant') {
                flush();
                out.push(node);
                continue;
            }
            episode.push(node.step);
            continue;
        }
        // subagent_group: close the current episode, then pass the group through.
        flush();
        out.push(node);
    }
    flush();

    // Duration repair for zero-span deep_step_groups. A single-frame thinking
    // passage is persisted as ONE row carrying ONE second-level timestamp, so its
    // startedAt === endedAt → a span of 0 → a misleading "用时 0.0 秒" for a whole
    // paragraph of reasoning. The real time the model spent on that episode is the
    // wall-clock until the NEXT node began (it was reasoning across that gap), so
    // estimate endedAt = the next node's start. Guarded by next > start so the
    // out-of-order subgraph timestamps (subagent-internal frames predate the
    // main-graph task frames) can never produce a negative span; an unrepairable
    // tail group keeps span 0 and the renderer drops its 用时 clause. Running
    // groups are left to the live ticker.
    const nodeStart = (n: TimelineNode): number | undefined => {
        if (n.kind === 'subagent_group') {
            let min: number | undefined;
            for (const a of n.agents) {
                for (const s of [a.step, ...a.children]) {
                    if (s.startedAt !== undefined) {
                        min = min === undefined ? s.startedAt : Math.min(min, s.startedAt);
                    }
                }
            }
            return min;
        }
        if (n.kind === 'deep_step_group') return n.startedAt;
        if (n.kind === 'intent') return n.startedAt;
        return n.step.startedAt;
    };
    for (let i = 0; i < out.length; i++) {
        const n = out[i];
        if (n.kind !== 'deep_step_group' || n.running) continue;
        if (n.startedAt === undefined) continue;
        if (n.endedAt !== undefined && n.endedAt > n.startedAt) continue; // already a real span
        for (let j = i + 1; j < out.length; j++) {
            const s = nodeStart(out[j]);
            if (s !== undefined) {
                if (s > n.startedAt) n.endedAt = s;
                break;
            }
        }
    }

    return out;
}

/**
 * True if `history` produces at least one renderable timeline node (深度思考组 /
 * subagent_group / intent). Carriers use this to suppress the "正在规划任务"
 * planning row once the session timeline has content: the moment deep-thinking
 * starts streaming, ExecutionTimeline shows it (and the live-tail keeps the last
 * node "正在深度思考" through the gap until the first task / clarify), so a
 * concurrent planning row is both redundant and misleading ("跳过澄清就规划"). We
 * test for ANY node, not a *running* one (isTimelineNodeRunning) — after a
 * thinking frame ends its step.running is false, yet planning should still defer
 * to the live-tail rather than reappear. Reuses the same build path as the
 * renderer so the "is there content" judgement can never drift from what shows.
 */
export function hasRenderableTimeline(history: ExecStepEventData[] | null | undefined): boolean {
    return buildTimelineGroups(mergeStepFrames(history)).length > 0;
}

/** True if a timeline node is still running (any agent / any step / the step). */
export function isTimelineNodeRunning(node: TimelineNode): boolean {
    if (node.kind === 'subagent_group') return node.agents.some((a) => a.step.running);
    if (node.kind === 'deep_step_group') return node.running;
    if (node.kind === 'intent') return false;
    return node.step.running;
}

/** True if a flow node is still running (any agent for a group; the step otherwise). */
export function isFlowNodeRunning(node: FlowNode): boolean {
    return node.kind === 'subagent_group' ? node.agents.some((a) => a.step.running) : node.step.running;
}

/**
 * The flow node a collapsed task header should summarize: the last running node,
 * or the most recent node if none is running. Returns null for an empty history.
 */
export function activeFlowNode(history: ExecStepEventData[] | null | undefined): FlowNode | null {
    // Exclude write_todos boundary markers AND call_user_input clarifies: both are
    // segment cuts, never a renderable header — returning one would let a consumer
    // render it as a row ("已更新任务清单" / a stray intent), breaking the same
    // contract buildTimelineGroups enforces (write_todos cut, clarify → IntentRow).
    const nodes = buildFlowNodes(mergeStepFrames(history)).filter(
        (n) => !(n.kind === 'step' && (isSegmentBoundary(n.step) || n.step.stepType === 'call_user_input')),
    );
    if (!nodes.length) return null;
    for (let i = nodes.length - 1; i >= 0; i--) {
        if (isFlowNodeRunning(nodes[i])) return nodes[i];
    }
    return nodes[nodes.length - 1];
}
