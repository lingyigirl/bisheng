// @ts-strict-ignore
import { createViewBatcher, flushSocketView, registerViewFlush } from "./viewBatcher";
import { getReplaySnapshot } from "~/api/harness";
import { useLocalize } from "~/hooks";
import { isNewerEvent } from "./replayCursor";
import { appendRecoveryStep, keepCompletedBeforeRecovery } from "./recoveryProjection";
import { closeTerminatedSteps, closeTerminatedTasks, terminationTime } from "./terminationProjection";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { getLinsightSessionVersionList, userInputLinsightEvent, userStopLinsightEvent } from "~/api/linsight";
import { SopStatus } from "~/store/linsight";
import { useToastContext } from "~/Providers";
import { toggleNav } from "~/utils";
import { buildTaskTree, mapSessionVersionStatus, useLinsightManager } from "../useLinsightManager";
import { MockWebSocket } from "./mock";
const MOCK = false

const connections: Record<string, WebSocket> = {};

export const useLinsightWebSocket = (versionId) => {
    const { getLinsight, updateLinsight } = useLinsightManager()
    const { showToast } = useToastContext();
    const retryAttempt = useRef(0);
    const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const cursors = useRef(new Map<string, string>());
    const connecting = useRef(new Set<string>());
    const localize = useLocalize();

    const linsight = getLinsight(versionId);
    const task = useMemo(() => {
        const linsight = getLinsight(versionId);
        return linsight
            ? { versionId, running: linsight.status === SopStatus.Running, replay: linsight.agent_config?.event_protocol === 2 }
            : { versionId, running: false, replay: false };
    }, [linsight?.status, linsight?.agent_config, versionId]);

    // 使用 ref 存储当前活跃版本 ID
    const activeVersionIdRef = useRef(versionId);
    // Live mirror of task.running, read inside the (stable, []-deps) connect
    // callback's onclose retry to avoid a stale closure and to gate reconnects.
    const runningRef = useRef(false);

    // 同步最新活跃版本 ID
    useEffect(() => {
        activeVersionIdRef.current = versionId;
        return () => {
            activeVersionIdRef.current = '';
            if (retryTimer.current) clearTimeout(retryTimer.current);
            retryTimer.current = null;
            const socket = connections[versionId];
            if (socket) {
                flushSocketView(socket);
                delete connections[versionId];
                socket.close();
            }
        };
    }, [versionId]);

    useEffect(() => {
        runningRef.current = task.running;
        if (!task.running && retryTimer.current) {
            clearTimeout(retryTimer.current);
            retryTimer.current = null;
        }
    }, [task.running]);

    const connect = useCallback(async (id: string, msg: any) => {
        if (connecting.current.has(id)) return;
        if (retryTimer.current) clearTimeout(retryTimer.current);
        retryTimer.current = null;
        const scheduleRetry = () => {
            if (activeVersionIdRef.current !== id || !runningRef.current) return;
            if (retryTimer.current) clearTimeout(retryTimer.current);
            const delay = Math.min(30000, 1000 * 2 ** Math.min(retryAttempt.current++, 5));
            retryTimer.current = setTimeout(() => {
                retryTimer.current = null;
                if (activeVersionIdRef.current === id && runningRef.current) void connect(id, { type: 'relink' });
            }, delay);
        };
        connecting.current.add(id);
        let snapshot;
        try {
            if (!cursors.current.has(id)) snapshot = await getReplaySnapshot(id);
        } catch (error) {
            connecting.current.delete(id);
            showToast({ message: localize('harness.loadError'), severity: 'error' });
            console.error('Replay snapshot failed', error);
            scheduleRetry();
            return;
        }
        connecting.current.delete(id);
        if (activeVersionIdRef.current !== id) return;
        // 清理可能存在的旧连接
        if (connections[id]) {
            flushSocketView(connections[id]); connections[id].close();
        }

        function getWebSocketUrl(path) {
            // Use environment variable if available, otherwise fallback to current origin
            const baseUrl = window.location.origin;

            // Ensure proper protocol (convert http -> ws, https -> wss)
            let url;
            if (baseUrl.startsWith('https://')) {
                url = `wss://${baseUrl.replace('https://', '')}`;
            } else if (baseUrl.startsWith('http://')) {
                url = `ws://${baseUrl.replace('http://', '')}`;
            } else {
                // If no protocol specified, use current page's protocol
                const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
                url = `${protocol}${baseUrl}`;
            }

            // Ensure path starts with slash
            const normalizedPath = path.startsWith('/') ? path : `/${path}`;

            return `${url}${normalizedPath}`;
        }

        let hydratingReplay = false;
        type ReplayState = NonNullable<ReturnType<typeof getLinsight>>;
        let replayState: ReplayState | null = null;
        let batchingText = false;
        const viewBatch = createViewBatcher<ReplayState>(update => updateLinsight(id, update));
        const updateView = (version: string, update: Partial<ReplayState> | ((current: ReplayState) => Partial<ReplayState>)) => {
            if (hydratingReplay && replayState) {
                replayState = { ...replayState, ...(typeof update === 'function' ? update(replayState) : update) };
            } else if (batchingText) viewBatch.enqueue(update);
            else { viewBatch.flush(); updateLinsight(version, update); }
        };
        const applyEvent = (taskData) => {
            if (taskData.event_id && !isNewerEvent(taskData.event_id, cursors.current.get(id))) return;
            batchingText = taskData.event_type === 'task_execute_step' &&
                ['thinking', 'assistant'].includes(taskData.data?.step_type) && taskData.data?.status !== 'end';
            if (!batchingText) viewBatch.flush();
            switch (taskData.event_type) {
                case 'task_recovery':
                    updateView(id, prev => ({
                        recoveryAttempt: taskData.data.attempt,
                        sessionSteps: keepCompletedBeforeRecovery(prev.sessionSteps || []),
                        tasks: prev.tasks.map(task => ({
                            ...task, history: keepCompletedBeforeRecovery(task.history || []),
                            children: (task.children || []).map(child => ({
                                ...child, history: keepCompletedBeforeRecovery(child.history || []),
                            })),
                        })),
                    }));
                    break;
                case 'task_generate':
                    // 生成一级任务
                    updateView(id, (prev) => {
                        // 创建顶级任务的浅拷贝
                        const updatedTasks = [...prev.tasks];

                        // 用于记录需要更新的父任务
                        const parentsToUpdate = new Map();

                        taskData.data.tasks.forEach(_task => {
                            if (_task.parent_task_id) {
                                // 处理子任务 - 收集到对应的父任务
                                const parentId = _task.parent_task_id;
                                if (!parentsToUpdate.has(parentId)) {
                                    parentsToUpdate.set(parentId, []);
                                }
                                parentsToUpdate.get(parentId).push({
                                    id: _task.id,
                                    // F035 deepagents tasks carry the title at task_data.name;
                                    // tolerate a flat name / legacy display_target too.
                                    name: _task.name ?? _task.task_data?.name ?? _task.task_data?.display_target ?? '',
                                    status: _task.status,
                                    history: [],
                                });
                            } else {
                                // 处理顶级任务 - 检查是否已存在
                                const exists = updatedTasks.some(t => t.id === _task.id);
                                if (!exists) {
                                    updatedTasks.push({
                                        id: _task.id,
                                        name: _task.name ?? _task.task_data?.name ?? _task.task_data?.display_target ?? '',
                                        status: _task.status,
                                        history: [],
                                        children: [],
                                    });
                                }
                            }
                        });

                        // 更新有子任务的父任务
                        parentsToUpdate.forEach((newChildren, parentId) => {
                            const parentIndex = updatedTasks.findIndex(t => t.id === parentId);
                            if (parentIndex === -1) return; // 父任务不存在

                            const parent = updatedTasks[parentIndex];
                            const existingChildIds = new Set(parent.children.map(c => c.id));

                            // 过滤掉已存在的子任务
                            const childrenToAdd = newChildren.filter(
                                child => !existingChildIds.has(child.id)
                            );

                            if (childrenToAdd.length > 0) {
                                // 创建新的父任务对象
                                updatedTasks[parentIndex] = {
                                    ...parent,
                                    children: [
                                        ...parent.children,
                                        ...childrenToAdd
                                    ]
                                };
                            }
                        });

                        // 返回全新的状态对象
                        return { tasks: updatedTasks };
                    });
                    break;
                case 'user_input': {
                    const { task_id, call_reason, params } = taskData.data;
                    updateView(id, (prev) => {
                        let matched = false;
                        const newTasks = prev.tasks.map(task => {
                            if (task.id === task_id) {
                                matched = true;
                                // 更新父任务
                                return {
                                    ...task,
                                    status: taskData.event_type,
                                    event_type: taskData.event_type,
                                    params,
                                    call_reason,
                                    history: [...(task.history || []), {
                                        ...taskData.data
                                    }]
                                };
                            } else {
                                return {
                                    ...task,
                                    // status: taskData.event_type,
                                    children: (task.children || []).map(child => {
                                        if (child.id === task_id) {
                                            matched = true;
                                            // 更新子任务
                                            return {
                                                ...child, // 关键修复：使用 child 而不是 task
                                                status: taskData.event_type,
                                                event_type: taskData.event_type,
                                                params,
                                                call_reason,
                                                history: [...(child.history || []), {
                                                    ...taskData.data
                                                }]
                                            };
                                        }
                                        return child;
                                    })
                                };
                            }
                        });
                        if (matched) return { tasks: newTasks };
                        // F035 Track H: session-level pseudo task (task_id == session_version_id),
                        // e.g. pre-planning intent clarification — keep it at flow level
                        return {
                            sessionSteps: [...(prev.sessionSteps || []), { step_type: 'call_user_input', ...taskData.data }]
                        };
                    });
                    break;
                }
                case 'user_input_completed': {
                    // F035 Track H: stamp the answered clarify entries so the active
                    // ClarifyCard collapses into an intent-summary row (spec §2).
                    const completedId = taskData.data.id ?? taskData.data.task_id;
                    const completedHistory = taskData.data.history || [];
                    const latestClarify = completedHistory
                        .filter((h: { step_type?: string; is_completed?: boolean }) => h?.step_type === 'call_user_input' && h.is_completed)
                        .at(-1);
                    const answer = latestClarify?.user_input ?? completedHistory
                        .filter((h: any) => h?.role === 'user')
                        .map((h: any) => h?.content)
                        .join('\n');
                    const markHistory = (history: any[]) => (history || []).map((h: any) =>
                        h?.step_type === 'call_user_input' && !h.is_completed
                            ? { ...h, is_completed: true, user_input: h.user_input || answer }
                            : h
                    );
                    updateView(id, (prev) => ({
                        tasks: prev.tasks.map(task =>
                            task.id === completedId
                                ? { ...task, history: markHistory(task.history || []) }
                                : {
                                    ...task,
                                    children: (task.children || []).map(child =>
                                        child.id === completedId
                                            ? { ...child, history: markHistory(child.history || []) }
                                            : child
                                    )
                                }
                        ),
                        sessionSteps: (prev.sessionSteps || []).map((s: any) =>
                            s?.step_type === 'call_user_input' && s.task_id === completedId && !s.is_completed
                                ? { ...s, is_completed: true, user_input: s.user_input || answer }
                                : s
                        ),
                    }));
                }
                // falls through: shared status update below
                // eslint-disable-next-line no-fallthrough
                case 'task_start':
                case 'task_end':
                    updateView(id, (prev) => {
                        const newStatus = taskData.data.status
                        const errorMsg = taskData.data.result?.answer
                        if (!taskData.data.parent_task_id) {
                            // 更新一级任务
                            const newTasks = prev.tasks.map(task =>
                                task.id === taskData.data.id
                                    ? { ...task, status: newStatus, errorMsg, event_type: taskData.event_type }
                                    : task
                            );
                            return { tasks: newTasks };
                        }

                        // 处理二级任务
                        const parentIndex = prev.tasks.findIndex(t => t.id === taskData.data.parent_task_id);
                        if (parentIndex === -1) return prev; // 父任务不存在

                        const parent = prev.tasks[parentIndex];

                        //  更新现有子任务
                        const newTasks = [...prev.tasks];
                        newTasks[parentIndex] = {
                            ...parent,
                            children: parent.children.map(child =>
                                child.id === taskData.data.id
                                    ? { ...child, status: newStatus, errorMsg: newStatus === 'failed' ? errorMsg : '', event_type: taskData.event_type }
                                    : child
                            )
                        };
                        return { tasks: newTasks };
                    });
                    break;
                case 'task_execute_step':
                    if (taskData.execution_epoch !== undefined) {
                        taskData = { ...taskData, data: { ...taskData.data, execution_epoch: taskData.execution_epoch } };
                    }
                    updateView(id, (prev) => {
                        let matched = false;
                        const newTasks = prev.tasks.map(task => {
                            if (task.id === taskData.data.task_id) {
                                matched = true;
                                return {
                                    ...task,
                                    history: appendRecoveryStep(task.history || [], taskData.data, hydratingReplay)
                                };
                            } else {
                                return {
                                    ...task,
                                    children: (task.children || []).map(child => {
                                        if (child.id === taskData.data.task_id) {
                                            matched = true;
                                            return {
                                                ...child,
                                                history: appendRecoveryStep(child.history || [], taskData.data, hydratingReplay)
                                            };
                                        }
                                        return child;
                                    })
                                };
                            }
                        });

                        if (matched) return { tasks: newTasks };
                        // F035 Track H: session-level steps (task_id == session_version_id,
                        // e.g. planning-stage tool calls) live at flow level
                        return {
                            sessionSteps: appendRecoveryStep(prev.sessionSteps || [], taskData.data, hydratingReplay)
                        };
                    });
                    break;
                case 'final_result':
                    updateView(id, {
                        output_result: taskData.data.output_result,
                        file_list: taskData.data.output_result.final_files || [],
                        status: SopStatus.completed
                    })
                    if (!hydratingReplay) toggleNav(true)
                    // Live only: the task answer is now a persisted category="task"
                    // ChatMessage. Pull its real id (+ liked verdict) into the store so
                    // like/dislike targets the right row the moment the result panel
                    // appears — without this the panel showed while the message still
                    // held its streaming placeholder id, so a like clicked before a
                    // reload was lost.
                    // The version-list already carries message_id (backend enrichment);
                    // history hydration seeds the store from the same endpoint.
                    {
                        const sessionId = getLinsight(id)?.session_id;
                        if (sessionId && !hydratingReplay) {
                            getLinsightSessionVersionList(sessionId, '')
                                .then((list: any[]) => {
                                    const v = (list || []).find((x) => String(x.id) === String(id));
                                    if (v?.message_id != null) {
                                        updateView(id, { message_id: v.message_id, liked: v.liked });
                                    }
                                })
                                .catch(() => { /* best-effort: a reload seeds it from the same endpoint */ });
                        }
                    }
                    break;
                case 'task_terminated':

                    updateView(id, prev => {
                        const stoppedAt = terminationTime(taskData.data.terminated_at, taskData.event_id);
                        return {
                            status: SopStatus.Stoped,
                            sessionSteps: closeTerminatedSteps(prev.sessionSteps || [], stoppedAt),
                            tasks: closeTerminatedTasks(prev.tasks, stoppedAt),
                        };
                    })
                    break;
                case 'error_message':
                    console.error(taskData.data.error, id, activeVersionIdRef.current)
                    if (id === activeVersionIdRef.current) {
                        updateView(id, {
                            taskError: taskData.data.error,
                            // 灵思LLM容错: structured classification drives the friendly
                            // error card; absent on older backends (card falls back).
                            taskErrorInfo: {
                                error_code: taskData.data.error_code,
                                error_type: taskData.data.error_type,
                                detail: taskData.data.detail,
                            },
                            status: SopStatus.Stoped
                        })
                    }
            }
            batchingText = false;
            if (taskData.event_id) cursors.current.set(id, taskData.event_id);
        };
        if (snapshot && !snapshot.legacy) {
            if (snapshot.complete_history) {
                replayState = { ...getLinsight(id)!, tasks: [], sessionSteps: [], output_result: null, file_list: [] };
                hydratingReplay = true;
                try {
                    for (const event of snapshot.snapshot.events) applyEvent(event);
                    updateLinsight(id, replayState);
                } catch (error) {
                    cursors.current.delete(id);
                    throw error;
                } finally {
                    hydratingReplay = false;
                    replayState = null;
                }
            } else {
                updateView(id, { tasks: buildTaskTree(snapshot.execute_tasks || []), sessionSteps: [], output_result: snapshot.version.output_result });
                showToast({ message: localize('harness.incomplete'), severity: 'warning' });
            }
            cursors.current.set(id, snapshot.last_event_id);
            updateView(id, { agent_config: snapshot.version.agent_config,
                recoveryAttempt: snapshot.version.recovery_state?.attempts,
                status: mapSessionVersionStatus(snapshot.version.status, snapshot.version.execute_feedback),
            });
            if (snapshot.terminal && snapshot.version.output_result) {
                const result = snapshot.version.output_result;
                updateView(id, { output_result: result, file_list: result.final_files || [],
                    ...(snapshot.version.status === 'failed' ? {
                        taskError: result.error_message,
                        taskErrorInfo: { error_type: result.error_type, detail: result.detail },
                    } : {}),
                });
            }
            if (snapshot.terminal) return;
        }
        const websocket = MOCK ? new MockWebSocket(`xx`)
            : new WebSocket(getWebSocketUrl(`${__APP_ENV__.BASE_URL}/api/v1/linsight/workbench/task-message-stream?session_version_id=${id}${cursors.current.has(id) ? `&after=${encodeURIComponent(cursors.current.get(id)!)}` : ""}`));
        connections[id] = websocket;

        websocket.onopen = () => {
            retryAttempt.current = 0;
            console.log("WebSocket connection established!");
            // websocket.send(JSON.stringify(msg));
        };

        websocket.onmessage = (event) => {
            const frame = JSON.parse(event.data);
            if (frame.event_type === 'replay_reset') {
                cursors.current.delete(id);
                delete connections[id];
                websocket.close();
                void connect(id, { type: 'reset' });
                return;
            }
            applyEvent(frame);
        };
        registerViewFlush(websocket as WebSocket, viewBatch.flush);
        websocket.onclose = () => {
            viewBatch.flush();
            console.log(`WebSocket closed for session ${id}`);
            if (connections[id] === websocket) {
                delete connections[id];
                scheduleRetry();
            }
        };

        websocket.onerror = (error) => {
            console.error(`WebSocket error for session ${id}:`, error);
        };
    }, [])

    useEffect(() => {
        if (!task.versionId) return;
        // Completed Harness turns also need the authoritative event history.
        // A terminal snapshot returns before opening a WebSocket; hydrate once.
        if (!task.running && (!task.replay || cursors.current.has(task.versionId))) return;

        // 当没有连接或连接已关闭时创建新连接
        if (!connections[task.versionId] ||
            connections[task.versionId].readyState !== WebSocket.OPEN) {
            connect(task.versionId, { type: 'init' });
        }
    }, [task])

    useEffect(() => {
        const handleOnline = () => {
            if (runningRef.current && activeVersionIdRef.current) {
                retryAttempt.current = 0;
                void connect(activeVersionIdRef.current, { type: 'online' });
            }
        };
        window.addEventListener('online', handleOnline);
        return () => window.removeEventListener('online', handleOnline);
    }, [connect]);

    const stop = useCallback(async () => {
        if (MOCK) {
            const ws = connections[versionId];
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'stop' }));
            }
        } else {
            try {
                await userStopLinsightEvent(versionId);
                // Fetch the authoritative terminal event even if the socket was
                // disconnected when stop was pressed. Never invent a stop time.
                cursors.current.delete(versionId);
                await connect(versionId, { type: 'stopped' });
            } catch (error) {
                console.error('Task stop failed', error);
                showToast({ message: localize('harness.loadError'), severity: 'error' });
            }
        }
        toggleNav(true)
    }, [versionId])

    const sendInput = useCallback(({ task_id, user_input, files }) => {
        if (MOCK) {
            const ws = connections[versionId];
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ user_input }));
            }
        } else {
            userInputLinsightEvent(versionId, task_id, user_input, files.map((file) => file.result))
            // update is_completed user_input files
            // @ts-ignore
            updateLinsight(versionId, (prev) => ({
                ...prev,
                // F035 Track H: optimistic stamp for session-level clarify entries too
                sessionSteps: (prev.sessionSteps || []).map((s: any) =>
                    s?.step_type === 'call_user_input' && !s.is_completed
                        ? { ...s, is_completed: true, user_input: s.user_input || user_input, files: s.files || files }
                        : s
                ),
                // Only stamp the answered task's OPEN clarify entry — mirrors the
                // WS `user_input_completed` handler. The previous version blanket-
                // marked every history entry of every task is_completed AND forced
                // the answered task to "success", but answering a clarify RESUMES
                // execution (≠ completion) and must not touch sibling tasks / steps.
                // Status is left to the WS stream.
                tasks: prev.tasks.map(task => {
                    const stampOpenClarify = (h: any) =>
                        h?.step_type === 'call_user_input' && !h.is_completed
                            ? { ...h, is_completed: true, user_input: h.user_input || user_input, files: h.files || files }
                            : h;
                    if (task.id === task_id) {
                        return { ...task, history: task.history?.map(stampOpenClarify) };
                    }
                    return {
                        ...task,
                        children: (task.children || []).map(child =>
                            child.id === task_id
                                ? { ...child, history: child.history?.map(stampOpenClarify) }
                                : child
                        ),
                    };
                }),
            }));
        }
    }, [versionId]);

    return { stop, sendInput };
};

