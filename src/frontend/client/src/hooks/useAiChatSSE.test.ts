import { renderHook, act } from "@testing-library/react";
import useAiChatSSE, { type SSESubmission } from "./useAiChatSSE";

// The hook registers its listeners on a `sse.js` instance; capture them so a
// test can drive frames by hand instead of opening a real connection.
const mockHandlers: Record<string, ((e: { data: string }) => void)[]> = {};

jest.mock("sse.js", () => ({
  SSE: jest.fn().mockImplementation(() => ({
    readyState: 1,
    addEventListener: (type: string, fn: (e: { data: string }) => void) => {
      mockHandlers[type] = mockHandlers[type] || [];
      mockHandlers[type].push(fn);
    },
    // The hook notifies "cancel" on unmount; a no-op is enough here.
    dispatchEvent: jest.fn(),
    stream: jest.fn(),
    close: jest.fn(),
  })),
}));

jest.mock("~/hooks", () => ({
  useLocalize: () => (key: string) => key,
}));

jest.mock("~/api/chatApi", () => ({
  getSSEUrl: () => "/api/v1/workstation/chat/completions",
}));

type AgentPatch = {
  text?: string;
  events?: { type: string; content?: string }[];
  finalised?: boolean;
  category?: string;
};

const push = (data: Record<string, unknown>) => {
  act(() => {
    (mockHandlers["message"] || []).forEach((fn) =>
      fn({ data: JSON.stringify(data) }),
    );
  });
};

const makeSubmission = (overrides: Partial<SSESubmission> = {}): SSESubmission =>
  ({
    payload: {},
    userMessage: { id: "u1" } as never,
    onCreated: jest.fn(),
    onMessage: jest.fn(),
    onAgentUpdate: jest.fn(),
    onFinal: jest.fn(),
    onError: jest.fn(),
    onStart: jest.fn(),
    onEnd: jest.fn(),
    ...overrides,
  }) as SSESubmission;

beforeEach(() => {
  Object.keys(mockHandlers).forEach((k) => delete mockHandlers[k]);
});

describe("useAiChatSSE — agent_answer frames", () => {
  // Backend behaviour under test: when an input hits the platform guardrail,
  // `/api/v1/workstation/chat/completions` replies with ONE
  // `agent_answer`/`over` frame carrying the close-out phrase and closes —
  // there is no `stream` phase and no `end` frame (see
  // `workstation/domain/services/chat_service.py::_blocked_input_stream`).
  // The hook used to fall through to a catch-all that emitted the still-empty
  // `responseText`, so the phrase was discarded and the user saw a blank
  // bubble despite the hit being logged server-side.
  it("renders the close-out phrase carried by an `over` frame", () => {
    const submission = makeSubmission();
    renderHook(() => useAiChatSSE(submission));

    // ASCII fixture on purpose: the production phrase is Chinese and lives in
    // the backend policy, so the exact characters are irrelevant here — what is
    // under test is whether the frame's `message.msg` survives to the UI at all.
    const phrase = "BLOCKED: input matched the platform policy";
    // Verbatim output of the backend's own `_sse_resp('agent_answer', 'over',
    // {'msg': reply}, cid)` (chat_helpers.py) — no message_id, no events.
    push({
      category: "agent_answer",
      type: "over",
      message: { msg: phrase },
      is_bot: true,
      chat_id: "c1",
      flow_id: "",
    });

    const onAgentUpdate = submission.onAgentUpdate as jest.Mock;
    expect(onAgentUpdate).toHaveBeenCalled();
    const patch = onAgentUpdate.mock.calls.at(-1)?.[0] as AgentPatch;
    expect(patch.text).toBe(phrase);
    // A blocked turn is complete the moment it arrives — leaving this false
    // would strand the row on the "thinking" indicator forever.
    expect(patch.finalised).toBe(true);
    expect(patch.category).toBe("agent_answer");
    expect(patch.events).toEqual([{ type: "text", content: phrase }]);

    // The legacy envelope feeds the pre-agent bubble; it must agree.
    expect(submission.onMessage).toHaveBeenCalledWith(phrase, "");
  });

  it("still stamps the final text from an `end` frame", () => {
    const submission = makeSubmission();
    renderHook(() => useAiChatSSE(submission));

    push({
      category: "agent_answer",
      type: "stream",
      message: { msg: "first half " },
    });
    push({
      category: "agent_answer",
      type: "end",
      message: { msg: "first half second half" },
      message_id: 42,
    });

    const onAgentUpdate = submission.onAgentUpdate as jest.Mock;
    const patch = onAgentUpdate.mock.calls.at(-1)?.[0] as AgentPatch;
    expect(patch.text).toBe("first half second half");
    expect(patch.finalised).toBe(true);
  });
});
