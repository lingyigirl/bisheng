import { atom } from 'recoil';

// F063: the org-KB tier (全部 / 不使用) rides on `selectedOrgKbs` as one synthetic
// marker entry instead of a new atom (Recoil is frozen — see `~/utils/knowledgeScope`).
export type OrgScope = 'all' | 'none';

export type SelectedOrgKb = {
  id: string;
  name: string;
  type: string;
  // Only the tier marker carries this; every other entry leaves it undefined, so
  // existing consumers are unaffected and an older client's archive reads back
  // as "never set" (which falls back — see `resolveOrgScope`).
  orgScope?: OrgScope;
};

// v2.5 Agent-mode: the subset of WorkstationConfig.tools the user has toggled on.
// Parent-level selection — one entry per parent group. Children (leaves) are
// flattened at request time so the backend still receives per-tool payloads.
export type SelectedAgentToolChild = {
  id: number;
  tool_key: string;
  name?: string;
  desc?: string;
};

export type SelectedAgentTool = {
  id: number;
  name: string;
  description?: string;
  children: SelectedAgentToolChild[];
};

const searchType = atom<string>({
  key: 'searchType',
  default: '',
});

const isSearch = atom<boolean>({
  key: 'isSearch',
  default: false,
});

// Persistence is handled by ChatView under `bs:{uid}:chatModel` so the value
// is user-scoped and gets cleared on re-login alongside the rest of `bs:*`.
const chatModel = atom<{ id: number; name: string }>({
  key: 'chatModel',
  default: { id: 0, name: '' },
});

const selectedOrgKbs = atom<SelectedOrgKb[]>({
  key: 'selectedOrgKbs',
  default: []
});

// v2.5: tools currently toggled on in the input bar. Session-scoped (not
// persisted) — each chat session starts from the admin-configured
// default_checked set in ChatView's initialisation effect.
const selectedAgentTools = atom<SelectedAgentTool[]>({
  key: 'selectedAgentTools',
  default: []
});

// Tracks whether selectedAgentTools has been initialised from bsConfig for
// the current session, so we don't repeatedly reset on every bsConfig update.
const agentToolsInitialized = atom<boolean>({
  key: 'agentToolsInitialized',
  default: false
});

const chatId = atom({
  key: 'chatId',
  default: ''
});

const enableOrgKb = atom<boolean>({
  key: 'enableOrgKb',
  default: false
});

const chatStatesMap = atom<Record<string, any>>({
  key: 'chatStatesMap',
  default: {}
});

export default {
  searchType,
  isSearch,
  chatModel,
  selectedOrgKbs,
  selectedAgentTools,
  agentToolsInitialized,
  enableOrgKb,
  chatId,
  chatStatesMap,
};
