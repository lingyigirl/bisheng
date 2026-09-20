import { DATASOURCE_ENGINES, type DatasourceEngine } from "@/controllers/API/datasource";

/**
 * Presentation facts about the six engines (spec AC-02).
 *
 * `defaultPort` is duplicated from the backend's `EngineSpec` on purpose: it is
 * what the form PRE-FILLS when an engine is picked, and the backend still treats
 * an absent port as "use the engine's own default". So a stale value here can
 * only ever show the wrong number in a field the admin can edit — it can never
 * change which port a saved datasource actually connects to.
 */
export const ENGINE_DEFAULT_PORT: Record<DatasourceEngine, number> = {
  mysql: 3306,
  postgresql: 5432,
  gaussdb: 5432,
  oracle: 1521,
  db2: 50000,
  dm: 5236,
};

/** i18n key suffix per engine, so the label reads "达梦 DM8" rather than "dm". */
export const ENGINE_LABEL: Record<DatasourceEngine, string> = {
  mysql: "mysql",
  postgresql: "postgresql",
  gaussdb: "gaussdb",
  oracle: "oracle",
  db2: "db2",
  dm: "dm",
};

export function isSupportedEngine(engine: string): engine is DatasourceEngine {
  return (DATASOURCE_ENGINES as readonly string[]).includes(engine);
}

/**
 * An unknown engine string still has to render. The backend can hold a row for
 * an engine a later release drops (the column is a plain string for exactly
 * that reason), so falling back to the raw value beats a blank cell.
 */
export function engineLabelKey(engine: string): string {
  return isSupportedEngine(engine) ? ENGINE_LABEL[engine] : engine;
}

export function defaultPortFor(engine: string): number | undefined {
  return isSupportedEngine(engine) ? ENGINE_DEFAULT_PORT[engine] : undefined;
}

export interface DatasourceFormValues {
  name: string;
  engine: string;
  host: string;
  port: string;
  database_name: string;
  username: string;
  password: string;
  description: string;
}

export const EMPTY_FORM: DatasourceFormValues = {
  name: "",
  engine: "mysql",
  host: "",
  port: String(ENGINE_DEFAULT_PORT.mysql),
  database_name: "",
  username: "",
  password: "",
  description: "",
};

/**
 * Switching engine rewrites the port only when the field still holds the
 * previous engine's default. An admin who typed a port deliberately keeps it —
 * silently overwriting 1521 with 3306 is how a working datasource gets broken
 * by an unrelated edit.
 */
export function withEngine(prev: DatasourceFormValues, engine: string): DatasourceFormValues {
  const wasDefault = prev.port === "" || prev.port === String(defaultPortFor(prev.engine) ?? "");
  const nextPort = defaultPortFor(engine);
  return {
    ...prev,
    engine,
    port: wasDefault && nextPort !== undefined ? String(nextPort) : prev.port,
  };
}

/**
 * An empty password field means "leave the stored one alone" on edit (the
 * backend reads an absent password the same way). Enforcing it here as well
 * keeps the two paths from disagreeing about what a blank field means.
 */
export function isFormSavable(values: DatasourceFormValues, isEdit: boolean): boolean {
  const credentialsComplete = isEdit || values.password.trim() !== "";
  return (
    values.name.trim() !== "" &&
    values.host.trim() !== "" &&
    values.database_name.trim() !== "" &&
    values.username.trim() !== "" &&
    credentialsComplete
  );
}
