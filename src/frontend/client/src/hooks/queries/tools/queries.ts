import { useQuery } from '@tanstack/react-query';
import { Constants, QueryKeys, dataService } from '~/types/chat';
import type { QueryObserverResult, UseQueryOptions } from '@tanstack/react-query';
import type * as t from '~/types/chat';
import { getKnowledgeInfo, getLinsightTools, getPersonalKnowledgeInfo } from '~/api/linsight';

export const useVerifyAgentToolAuth = (
  params: t.VerifyToolAuthParams,
  config?: UseQueryOptions<t.VerifyToolAuthResponse>,
): QueryObserverResult<t.VerifyToolAuthResponse> => {
  return useQuery<t.VerifyToolAuthResponse>(
    [QueryKeys.toolAuth, params.toolId],
    () => dataService.getVerifyAgentToolAuth(params),
    {
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchOnMount: false,
      ...config,
    },
  );
};

export const useGetToolCalls = <TData = t.ToolCallResults>(
  params: t.GetToolCallParams,
  config?: UseQueryOptions<t.ToolCallResults, unknown, TData>,
): QueryObserverResult<TData, unknown> => {
  const { conversationId = '' } = params;
  return useQuery<t.ToolCallResults, unknown, TData>({
    queryKey: [QueryKeys.toolCalls, conversationId],
    queryFn: () => dataService.getToolCalls(params),
    ...{
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchOnMount: false,
      enabled:
        conversationId.length > 0 &&
        conversationId !== Constants.NEW_CONVO &&
        conversationId !== Constants.SEARCH,
      ...config,
    },
  });
};


// 灵思内置工具列表
export const useGetLinsightToolList = () => {
  return useQuery({
    queryKey: ['LinsightTools'],
    queryFn: getLinsightTools,
    select(data) {
      return data?.data;
    },
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}

// 获取个人知识库工具
export const useGetPersonalToolList = () => {
  return useQuery({
    queryKey: ['PersonalTools'],
    queryFn: () => getPersonalKnowledgeInfo(),
    select(data) {
      return data?.data;
    },
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

// 获取组织知识库
export const useGetOrgToolList = (query: { page, page_size?, name?, sort_by?, preferred_ids?: string }) => {
  return useQuery({
    // preferred_ids participates in the key so switching configured set (or
    // loading bsConfig.orgKbs after the first render) invalidates the cache.
    // page_size MUST be here too: the app has one QueryClient shared by every
    // route, and callers disagree on the size (F063's existence probe asks for 1;
    // the linsight org picker asks for 50). Without it in the key they collapse
    // into one entry and whoever mounts first decides what the others render —
    // the probe would truncate the picker to a single row.
    queryKey: ['OrgTools', query.page, query.page_size, query.name, query.preferred_ids || ''],
    queryFn: () => getKnowledgeInfo(query),
    select(data) {
      return data?.data.data;
    },
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}