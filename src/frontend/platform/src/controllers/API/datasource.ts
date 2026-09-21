import axios from "../request";

/**
 * F061 datasource API. Mirrors `bisheng/datasource/api/endpoints/`.
 *
 * Two shapes are deliberately absent from the write payloads, and the backend
 * rejects them by construction (spec AC-07): there is no raw connection-string
 * field, and no "save a SQL statement as a queryable object" call. Adding one
 * here would be the first step to losing that property, so they are not
 * "just in case" modelled.
 */

/** Mirrors the backend's `SUPPORTED_ENGINES` (sqlite is rejected, spec AC-06). */
export const DATASOURCE_ENGINES = [
  "mysql",
  "postgresql",
  "gaussdb",
  "oracle",
  "db2",
  "dm",
] as const;

export type DatasourceEngine = (typeof DATASOURCE_ENGINES)[number];

export interface DatasourceRow {
  id: number;
  name: string;
  engine: string;
  host: string;
  port: number;
  database_name: string;
  username: string;
  description?: string | null;
  /** 1 = account has write permission, 0 = not, null = not yet probed. */
  writable_account?: number | null;
  enabled: number;
  /** Never the password itself — the read path redacts it (INV-19 clause 1). */
  has_password: boolean;
  create_time?: string;
  update_time?: string;
}

export interface DatasourceConnectionPayload {
  host: string;
  /** Omitted means "use the engine's own default port" (5236 for DM8, …). */
  port?: number | null;
  database_name: string;
  username: string;
  /** Empty on edit means "keep the stored password unchanged". */
  password?: string;
}

export interface DatasourceCreatePayload {
  name: string;
  engine: string;
  connection: DatasourceConnectionPayload;
  description?: string | null;
}

export interface DatasourceUpdatePayload {
  name?: string;
  connection?: DatasourceConnectionPayload;
  description?: string | null;
  enabled?: number;
}

export interface DatasourceConnectionTestResult {
  ok: boolean;
  /** The branchable part; `message` and `detail` are for humans. */
  category: string;
  message: string;
  detail?: string;
  writable?: boolean | null;
}

export interface DatasourceColumn {
  name?: string | null;
  type?: string | null;
  /**
   * Absent (not empty) on a dialect that cannot report comments — the UI must
   * tell those two apart (spec AC-14), so this stays optional rather than
   * defaulting to "".
   */
  comment?: string | null;
}

export interface DatasourceTableRow {
  id: number;
  table_name: string;
  table_comment?: string | null;
  structure_snapshot: DatasourceColumn[];
  business_desc?: string | null;
  enabled: number;
}

/** A row of `GET /{id}/available-tables` — a live read of the customer's DB. */
export interface AvailableTableRow {
  table_name: string;
  collected: boolean;
  enabled: number;
}

export interface QueryFailureRow {
  id: number;
  datasource_id: number;
  statements: string[];
  tables: string[];
  error_category: string;
  create_time: string;
}

export interface PagedResult<T> {
  data: T[];
  total: number;
  page?: number;
  page_size?: number;
}

export async function listDatasourcesApi(params: {
  page?: number;
  page_size?: number;
  enabled_only?: boolean;
  /** Substring match on the name; blank/omitted means "no filter". */
  name?: string;
}): Promise<PagedResult<DatasourceRow>> {
  return await axios.get(`/api/v1/datasource`, { params });
}

/**
 * The datasources the CURRENT USER may pick, for a picker (F061 AC-53).
 *
 * Not `listDatasourcesApi`: that one is the admin management list and is gated
 * on `get_tenant_admin_user`, while the flow editor at `/flow/:id` has no
 * permission gate — so an ordinary flow author would get a 403. This endpoint
 * is open to any logged-in user and returns only what they hold `viewer` on.
 */
export async function listAvailableDatasourcesApi(): Promise<
  PagedResult<Pick<DatasourceRow, "id" | "name" | "description" | "engine">>
> {
  return await axios.get(`/api/v1/datasource/available-datasources`);
}

export async function getDatasourceApi(id: number): Promise<DatasourceRow> {
  return await axios.get(`/api/v1/datasource/${id}`);
}

export async function createDatasourceApi(
  payload: DatasourceCreatePayload,
): Promise<DatasourceRow> {
  return await axios.post(`/api/v1/datasource`, payload);
}

export async function updateDatasourceApi(
  id: number,
  payload: DatasourceUpdatePayload,
): Promise<DatasourceRow> {
  return await axios.put(`/api/v1/datasource/${id}`, payload);
}

export async function deleteDatasourceApi(id: number): Promise<{ deleted: number }> {
  return await axios.delete(`/api/v1/datasource/${id}`);
}

/**
 * Probe a connection that may not exist yet, then close it. Persists nothing.
 *
 * Answers 200 with `ok: false` rather than an error response: a failed test is
 * the expected outcome of a mistyped password, so the caller branches on `ok`.
 */
export async function testDatasourceConnectionApi(payload: {
  engine: string;
  host: string;
  port?: number | null;
  database_name: string;
  username: string;
  password?: string;
}): Promise<DatasourceConnectionTestResult> {
  return await axios.post(`/api/v1/datasource/test-connection`, payload);
}

/** Tables + views the configured account can see right now (spec AC-09). */
export async function listAvailableTablesApi(
  datasourceId: number,
): Promise<PagedResult<AvailableTableRow>> {
  return await axios.get(`/api/v1/datasource/${datasourceId}/available-tables`);
}

/** The collected snapshot — what the model actually sees. */
export async function listDatasourceTablesApi(
  datasourceId: number,
): Promise<PagedResult<DatasourceTableRow>> {
  return await axios.get(`/api/v1/datasource/${datasourceId}/tables`);
}

export async function selectDatasourceTablesApi(
  datasourceId: number,
  tableNames: string[],
): Promise<{ changed: number }> {
  return await axios.post(`/api/v1/datasource/${datasourceId}/tables/select`, {
    table_names: tableNames,
  });
}

/** Re-collect structure. Must NOT touch `business_desc` (spec AC-12). */
export async function collectDatasourceTablesApi(
  datasourceId: number,
  tableNames: string[],
): Promise<{ created: number; refreshed: number; truncated?: boolean }> {
  return await axios.post(`/api/v1/datasource/${datasourceId}/collect`, {
    table_names: tableNames,
  });
}

export async function updateTableDescriptionApi(
  tableId: number,
  businessDesc: string,
): Promise<{ id: number }> {
  return await axios.put(`/api/v1/datasource/table-metadata/${tableId}`, {
    business_desc: businessDesc,
  });
}

export async function listQueryFailuresApi(params: {
  datasource_id?: number;
  error_category?: string;
  page?: number;
  page_size?: number;
}): Promise<PagedResult<QueryFailureRow>> {
  return await axios.get(`/api/v1/datasource/query-failures`, { params });
}
