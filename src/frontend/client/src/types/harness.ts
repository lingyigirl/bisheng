export type SubagentConfig = {
    id: string;
    name: string;
    description: string;
    system_prompt: string;
    model_id: string | null;
    skill_names: string[];
    tool_refs: number[];
    connector_refs: number[];
};

export type AgentConfig = {
    schema_version: 1;
    subagents: SubagentConfig[];
};

export type HarnessOptions = {
    models?: { id: string | number; name?: string; displayName?: string }[];
    tools?: { id: number; name: string; is_preset?: number; children?: { id: number; name: string }[] }[];
};

/** F087: availability report attached to a saved member by the library list. */
export type SavedMemberIssues = {
    tools?: number[];
    skills?: string[];
    model?: boolean;
};

/** F087: one row of the user's saved collaborator library. */
export type SavedMember = {
    id: number;
    name: string;
    description: string;
    system_prompt: string;
    model_id: string | null;
    skill_names: string[];
    tool_refs: number[];
    connector_refs: number[];
    /** F087: starred members are auto-checked into new conversations. */
    is_default?: boolean;
    issues?: SavedMemberIssues;
    create_time?: string | null;
    update_time?: string | null;
};

/** Payload for saving a member; ``id`` set -> update that owned library row. */
export type SavedMemberPayload = Omit<SubagentConfig, 'id'> & { id?: number };
