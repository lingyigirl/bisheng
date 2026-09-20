import { Button } from "@/components/bs-ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/bs-ui/dialog";
import { Input, PasswordInput, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/bs-ui/select";
import { Badge } from "@/components/bs-ui/badge";
import {
  createDatasourceApi,
  testDatasourceConnectionApi,
  updateDatasourceApi,
  DATASOURCE_ENGINES,
  type DatasourceConnectionTestResult,
  type DatasourceRow,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  EMPTY_FORM,
  engineLabelKey,
  isFormSavable,
  type DatasourceFormValues,
  withEngine,
} from "./datasourceConstants";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present when editing; absent when creating. */
  editing?: DatasourceRow | null;
  onSaved: () => void;
}

function toFormValues(row: DatasourceRow): DatasourceFormValues {
  return {
    name: row.name,
    engine: row.engine,
    host: row.host,
    port: String(row.port ?? ""),
    database_name: row.database_name,
    username: row.username,
    // Never pre-filled: the read path does not return the password (INV-19 §1).
    // Blank means "keep the stored one", which the backend agrees with.
    password: "",
    description: row.description ?? "",
  };
}

export function DatasourceFormDialog({ open, onOpenChange, editing, onSaved }: Props) {
  const { t } = useTranslation();
  const [values, setValues] = useState<DatasourceFormValues>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<DatasourceConnectionTestResult | null>(null);

  const isEdit = Boolean(editing);

  useEffect(() => {
    if (!open) return;
    setValues(editing ? toFormValues(editing) : EMPTY_FORM);
    setTestResult(null);
  }, [open, editing]);

  const patch = useCallback((next: Partial<DatasourceFormValues>) => {
    setValues((prev) => ({ ...prev, ...next }));
    // Any edit invalidates the previous probe: the badge would otherwise keep
    // asserting "connection OK" about details that are no longer on the form.
    setTestResult(null);
  }, []);

  const connectionPayload = () => ({
    host: values.host.trim(),
    // An empty field means "use the engine's own default", the same reading the
    // backend applies to an absent port.
    port: values.port.trim() === "" ? null : Number(values.port),
    database_name: values.database_name.trim(),
    username: values.username.trim(),
  });

  const handleTest = async () => {
    setTesting(true);
    const res = await captureAndAlertRequestErrorHoc(
      testDatasourceConnectionApi({
        engine: values.engine,
        ...connectionPayload(),
        password: values.password,
      }),
    );
    setTesting(false);
    if (res) setTestResult(res);
  };

  const handleSave = async () => {
    setSaving(true);
    const common = {
      name: values.name.trim(),
      connection: connectionPayload(),
      description: values.description.trim() === "" ? null : values.description.trim(),
    };
    const res = editing
      ? await captureAndAlertRequestErrorHoc(
          updateDatasourceApi(editing.id, {
            ...common,
            // Omitted rather than sent empty: `password: ""` is indistinguishable
            // from "clear it" on the wire, and clearing is not supported.
            ...(values.password === "" ? {} : { connection: { ...common.connection, password: values.password } }),
          }),
        )
      : await captureAndAlertRequestErrorHoc(
          createDatasourceApi({ ...common, engine: values.engine, connection: { ...common.connection, password: values.password } }),
        );
    setSaving(false);
    if (!res) return;
    onSaved();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? t("datasource.form.editTitle") : t("datasource.form.createTitle")}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {isEdit ? t("datasource.form.editTitle") : t("datasource.form.createTitle")}
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[60vh] gap-3 overflow-y-auto py-2">
          <div>
            <Label>{t("datasource.form.name")}</Label>
            <Input
              className="mt-1"
              value={values.name}
              onChange={(e) => patch({ name: e.target.value })}
              placeholder={t("datasource.form.namePlaceholder")}
            />
          </div>

          <div>
            <Label>{t("datasource.form.engine")}</Label>
            {/* The engine is fixed once saved: changing it would silently
                reinterpret every stored field (a port that meant 3306 now
                means 5236), so the backend refuses it and the form does too. */}
            <Select
              value={values.engine}
              disabled={isEdit}
              onValueChange={(engine) => patch(withEngine(values, engine))}
            >
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATASOURCE_ENGINES.map((engine) => (
                  <SelectItem key={engine} value={engine}>
                    {t(`datasource.engine.${engineLabelKey(engine)}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {isEdit && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("datasource.form.engineImmutable")}
              </p>
            )}
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <Label>{t("datasource.form.host")}</Label>
              <Input
                className="mt-1"
                value={values.host}
                onChange={(e) => patch({ host: e.target.value })}
                placeholder={t("datasource.form.hostPlaceholder")}
              />
            </div>
            <div className="w-[130px]">
              <Label>{t("datasource.form.port")}</Label>
              <Input
                className="mt-1"
                value={values.port}
                inputMode="numeric"
                onChange={(e) => patch({ port: e.target.value.replace(/\D/g, "") })}
                placeholder={t("datasource.form.portPlaceholder")}
              />
            </div>
          </div>

          <div>
            <Label>{t("datasource.form.databaseName")}</Label>
            <Input
              className="mt-1"
              value={values.database_name}
              onChange={(e) => patch({ database_name: e.target.value })}
              placeholder={t("datasource.form.databaseNamePlaceholder")}
            />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <Label>{t("datasource.form.username")}</Label>
              <Input
                className="mt-1"
                value={values.username}
                onChange={(e) => patch({ username: e.target.value })}
                placeholder={t("datasource.form.usernamePlaceholder")}
              />
            </div>
            <div className="flex-1">
              <Label>{t("datasource.form.password")}</Label>
              <PasswordInput
                className="mt-1"
                value={values.password}
                onChange={(e) => patch({ password: e.target.value })}
                placeholder={
                  isEdit ? t("datasource.form.passwordKeepPlaceholder") : t("datasource.form.passwordPlaceholder")
                }
              />
            </div>
          </div>

          <div>
            <Label>{t("datasource.form.description")}</Label>
            <Textarea
              className="mt-1"
              rows={3}
              value={values.description}
              onChange={(e) => patch({ description: e.target.value })}
            />
          </div>

          <p className="text-xs text-muted-foreground">{t("datasource.form.readonlyHint")}</p>

          <div className="flex items-center gap-3">
            <Button type="button" variant="outline" disabled={testing} onClick={handleTest}>
              {testing ? t("datasource.form.testing") : t("datasource.form.testConnection")}
            </Button>
            {testResult && (
              <div className="min-w-0 flex-1 text-xs">
                <span className={testResult.ok ? "text-green-600" : "text-red-500"}>
                  {testResult.message}
                </span>
                {testResult.detail && (
                  <p className="mt-0.5 break-all text-muted-foreground">{testResult.detail}</p>
                )}
              </div>
            )}
          </div>

          {/* AC-05: a writable account is surfaced and does NOT block saving.
              The platform's boundary is the account's own grants (INV-19 §4),
              and refusing a writable account would push customers to bypass the
              feature instead. */}
          {testResult?.ok && testResult.writable && (
            <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
              <Badge variant="gray" className="shrink-0">
                {t("datasource.form.writableBadge")}
              </Badge>
              <span>{t("datasource.form.writableWarning")}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" className="px-11" onClick={() => onOpenChange(false)}>
            {t("datasource.form.cancel")}
          </Button>
          <Button
            type="button"
            className="px-11"
            disabled={saving || !isFormSavable(values, isEdit)}
            onClick={handleSave}
          >
            {t("datasource.form.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
