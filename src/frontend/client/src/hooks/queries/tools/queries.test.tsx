/**
 * F063 review (H1) — `useGetOrgToolList` must key on `page_size`.
 *
 * The app mounts ONE QueryClient for every route, and the callers of this hook
 * disagree on the page size: F063's existence probe asks for 1, the linsight
 * organization picker asks for 50. With `page_size` absent from the key they
 * collapse into a single cache entry and whichever mounts first decides what the
 * others render — the probe truncating the task-mode picker to one row, which
 * violates spec AC-25 ("the task-mode execution state is unchanged").
 *
 * Why assert on the cache rather than on rendered output: every component test
 * in this app mocks the query layer away, so the collision is invisible to them
 * (all four new F063 suites stayed green while it was live). Counting cache
 * entries is the property itself, not a proxy for it.
 */

import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useGetOrgToolList } from './queries';

jest.mock('~/api/linsight', () => ({
  getKnowledgeInfo: jest.fn(async () => ({ data: { data: [] } })),
  getLinsightTools: jest.fn(async () => ({ data: { data: [] } })),
  getPersonalKnowledgeInfo: jest.fn(async () => ({ data: { data: [] } })),
}));

const wrapperFor = (client: QueryClient) =>
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };

const freshClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('useGetOrgToolList cache key (F063 review H1)', () => {
  it('keeps a page_size:1 probe and a page_size:50 picker in separate entries', () => {
    const client = freshClient();
    const wrapper = wrapperFor(client);

    renderHook(() => useGetOrgToolList({ page: 1, page_size: 1 }), { wrapper });
    renderHook(() => useGetOrgToolList({ page: 1, page_size: 50 }), { wrapper });

    // One entry here means the picker is served the probe's single row.
    expect(client.getQueryCache().getAll()).toHaveLength(2);
  });

  it('keeps the two F063 probes sharing one entry with each other', () => {
    // The positive control for the case above: the fix must not make every call
    // unique. `AiChatInput` and `AttachmentBar` ask with identical arguments so
    // that mounting both issues one request, not two (design 决策 12).
    const client = freshClient();
    const wrapper = wrapperFor(client);

    renderHook(() => useGetOrgToolList({ page: 1, page_size: 1 }), { wrapper });
    renderHook(() => useGetOrgToolList({ page: 1, page_size: 1 }), { wrapper });

    expect(client.getQueryCache().getAll()).toHaveLength(1);
  });

  it('still separates callers that differ on an argument that was already keyed', () => {
    const client = freshClient();
    const wrapper = wrapperFor(client);

    renderHook(() => useGetOrgToolList({ page: 1, page_size: 1 }), { wrapper });
    renderHook(() => useGetOrgToolList({ page: 2, page_size: 1 }), { wrapper });

    expect(client.getQueryCache().getAll()).toHaveLength(2);
  });
});
