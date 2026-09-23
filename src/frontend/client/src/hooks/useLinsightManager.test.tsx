/**
 * F063 (review H1') — the landing page's org-KB tier must actually reach the wire.
 *
 * The landing page cannot enumerate the user's organization knowledge bases, so
 * its 「全部」 tier travels as `organization_knowledge_all` and the server
 * materialises the permitted ids at submit time. The test asserts on the SSE
 * payload because that payload IS the contract: the landing page used to send
 * only `org_knowledge_enabled`, a boolean the task run documents as
 * "intentionally NOT consulted" (`task_exec._resolve_user_knowledge_bases`), so
 * the tier and its summary card had no effect on what was searched.
 *
 * Case 2 is the AC-25 guard: the two other producers of `submissionState('new')`
 * — the execution-state input and the daily-chat linsight entry — never set the
 * field, and must keep reading as "no expansion" rather than "field forgotten".
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from '@testing-library/react';
// Recoil is frozen for PRODUCTION code; this file only mounts a root so the hook
// under test can read the existing `submissionState` atom — there is no non-Recoil
// way to render it (same scaffolding as `test/layout-test-utils.tsx`).
// eslint-disable-next-line no-restricted-imports
import { RecoilRoot } from 'recoil';
import { SSE } from 'sse.js';
import { submissionState, type SubmissionState } from '~/store/linsight';
import { useLinsightSubmit } from './useLinsightManager';

// Factory, not automock: `sse.js` ships ESM only and is not in the jest
// transform allow-list, so merely requiring it to derive a mock shape throws.
jest.mock('sse.js', () => ({ SSE: jest.fn() }));
jest.mock('~/Providers', () => ({
  useToastContext: () => ({ showToast: jest.fn() }),
}));
jest.mock('~/utils', () => ({
  addConversation: jest.fn(),
  formatTime: jest.fn(() => ''),
  toggleNav: jest.fn(),
}));

const baseSubmission = (): SubmissionState => ({
  isNew: true,
  files: [],
  question: 'what is the expense policy',
  tools: [],
  model: '',
  skills: [],
  enableWebSearch: false,
  useKnowledgeBase: true,
});

/** Renders the submit driver and returns the JSON body of the SSE it opened. */
const capturedPayload = (submission: SubmissionState) => {
  const payloads: string[] = [];
  (SSE as unknown as jest.Mock).mockImplementation((_url: string, opts: { payload: string }) => {
    payloads.push(opts.payload);
    return { addEventListener: jest.fn(), close: jest.fn(), stream: jest.fn() };
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderHook(() => useLinsightSubmit('new', jest.fn(), jest.fn()), {
    wrapper: ({ children }) => (
      <RecoilRoot initializeState={({ set }) => set(submissionState('new'), submission)}>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </RecoilRoot>
    ),
  });

  // Nothing else may open an SSE here: a second one would mean the effect ran
  // twice and this assertion is reading a payload nobody sent.
  expect(payloads).toHaveLength(1);
  return JSON.parse(payloads[0]);
};

describe('useLinsightSubmit payload — F063 org-KB tier (H1\')', () => {
  it("sends organization_knowledge_all when the tier is 'all'", () => {
    const body = capturedPayload({ ...baseSubmission(), organizationKnowledgeAll: true });

    expect(body.organization_knowledge_all).toBe(true);
  });

  it('sends an explicit false when the producer never set the tier', () => {
    const body = capturedPayload(baseSubmission());

    expect(body.organization_knowledge_all).toBe(false);
  });

  it("sends false when the tier was explicitly 'none'", () => {
    const body = capturedPayload({ ...baseSubmission(), organizationKnowledgeAll: false });

    expect(body.organization_knowledge_all).toBe(false);
  });
});
