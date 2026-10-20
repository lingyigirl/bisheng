/**
 * ExecutionLiveContext — whether the surrounding execution turn is still live
 * (session status === Running).
 *
 * Why this exists: a group's "running" is derived from per-step `running`, which
 * stays true until a `status:'end'` frame arrives. A safety-blocked / skipped
 * subagent step never gets that end frame, so its group would otherwise show a
 * stuck "正在派出…（已用 N 秒）…" with a forever-ticking clock long after the whole
 * task finished. Only the carrier (ExecutionFlow / TaskTurnPanel / a historical
 * ConversationRound) knows the turn-level status, so it provides this flag and
 * the timeline groups gate their effective running on it: a non-live turn means
 * NOTHING is actually running, regardless of dangling step frames.
 *
 * Default = true so an unwrapped render preserves the raw step.running behavior.
 */
import { createContext, useContext } from 'react';

export const ExecutionLiveContext = createContext(true);

/** True while the surrounding execution turn is still live (status Running). */
export const useExecutionLive = (): boolean => useContext(ExecutionLiveContext);

/**
 * ExecutionTurnLiveContext — TURN-level liveness for subagent cards.
 *
 * Separate from ExecutionLiveContext on purpose. That flag doubles as the
 * "planning phase is over" signal (false once the task list exists), which is
 * correct for collapsing the session-level thinking groups but wrong for a
 * subagent card: a delegation step routinely streams in the session-level
 * timeline while tasks already exist, so gating the card on the planning flag
 * rendered a RUNNING subagent as "已停止 · Ns" (reported 2026-10-14, again
 * 2026-10-15 with a live 资料研究员). Cards read THIS context instead: true
 * while the turn is running and not parked on ask_user; a historical round and
 * a finished task re-provide false so a dangling step frame still collapses to
 * "已停止".
 *
 * Default = true so an unwrapped render preserves the raw step.running behavior.
 */
export const ExecutionTurnLiveContext = createContext(true);

/** True while the surrounding turn (or task row) is still actually running. */
export const useExecutionTurnLive = (): boolean => useContext(ExecutionTurnLiveContext);
