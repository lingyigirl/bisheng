import { Button } from "@/components/bs-ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/bs-ui/dialog";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import {
  updateTableDescriptionApi,
  type DatasourceTableRow,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface Props {
  table: DatasourceTableRow;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

/** One field of the collected snapshot: its name, what the database says, and
 *  the admin's correction (editable). */
interface FieldRowProps {
  name: string;
  dbComment?: string | null;
  value: string;
  onChange: (next: string) => void;
}

function FieldRow({ name, dbComment, value, onChange }: FieldRowProps) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-border-base p-2">
      <div className="flex items-center gap-2">
        <span className="text-body-sm font-medium text-text-1">{name}</span>
        {value.trim() !== "" && (
          <span className="rounded bg-blue-500/[0.08] px-1.5 py-0.5 text-caption text-blue-600">
            {t("datasource.description.fieldOverridden")}
          </span>
        )}
      </div>
      {/* The collected value has to be readable on the SAME screen as the input
          (AC-08): "what should I write here?" is unanswerable otherwise. It is
          display-only -- writing it back to the customer database is what
          INV-19 ② exists to prevent. */}
      <p className="mt-1 text-caption text-text-3">
        {t("datasource.description.fieldDbComment")}:{" "}
        {dbComment ? dbComment : t("datasource.description.fieldNoDbComment")}
      </p>
      <Input
        className="mt-1.5"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t("datasource.description.fieldOverridePlaceholder")}
      />
    </div>
  );
}

/**
 * The business description an admin writes for a table (spec AC-11), and — since
 * F064 — a correction per field.
 *
 * This is the whole governance loop: a failing table shows up in the failure
 * list (AC-42), the admin lands here, and what they write is what the model is
 * given next time. `refresh_tables` never writes these columns, so a re-collect
 * cannot undo either of them (AC-12, F064 AC-04).
 *
 * Both are edited here and saved in ONE request, which is why the endpoint
 * takes both fields: two save buttons would leave a half-finished edit behind
 * whenever the second call failed.
 */
export function TableDescriptionDialog({ table, onOpenChange, onSaved }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(table.business_desc ?? "");
  const [overrides, setOverrides] = useState<Record<string, string>>(
    () => table.column_overrides ?? {},
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(table.business_desc ?? "");
    setOverrides(table.column_overrides ?? {});
  }, [table]);

  const columns = table.structure_snapshot ?? [];
  const snapshotNames = new Set(columns.map((column) => column.name));

  // Fields the admin corrected that the database no longer has (AC-05). Kept
  // and shown rather than dropped: the note still means something to whoever
  // wrote it, and a rename is not a reason to discard it silently. Rendered
  // AFTER the live fields so the list still reads in schema order.
  const orphaned = Object.keys(overrides).filter((name) => !snapshotNames.has(name));

  const setOverride = (name: string, next: string) =>
    setOverrides((prev) => ({ ...prev, [name]: next }));

  const handleSave = async () => {
    setSaving(true);
    // Blank values are dropped server-side (决策 5), so clearing an input is how
    // an override is removed -- that is the whole undo path, and it is why the
    // hint below says so rather than leaving the admin to discover it.
    const res = await captureAndAlertRequestErrorHoc(
      updateTableDescriptionApi(table.id, value.trim(), overrides),
    );
    setSaving(false);
    if (res) onSaved();
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>
            {t("datasource.description.title", { table: table.table_name })}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t("datasource.description.hint")}
          </DialogDescription>
        </DialogHeader>

        <div className="py-2">
          <Label>{t("datasource.description.label")}</Label>
          <Textarea
            className="mt-1"
            rows={4}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={t("datasource.description.placeholder")}
          />
          {table.table_comment && (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("datasource.description.dbComment")}: {table.table_comment}
            </p>
          )}
        </div>

        {columns.length > 0 && (
          <div className="py-2">
            <Label>{t("datasource.description.fieldsLabel")}</Label>
            <p className="mt-1 text-caption text-text-3">
              {t("datasource.description.fieldsHint")}
            </p>
            <div className="mt-2 max-h-[300px] space-y-2 overflow-auto pr-1">
              {columns.map((column, index) => (
                <FieldRow
                  key={column.name ?? `column-${index}`}
                  name={column.name ?? ""}
                  dbComment={column.comment}
                  value={overrides[column.name ?? ""] ?? ""}
                  onChange={(next) => setOverride(column.name ?? "", next)}
                />
              ))}
              {orphaned.map((name) => (
                <div key={name} className="rounded-md border border-dashed border-border-deep p-2">
                  <div className="flex items-center gap-2">
                    <span className="text-body-sm font-medium text-text-2">{name}</span>
                    <span className="rounded bg-fill-3 px-1.5 py-0.5 text-caption text-text-3">
                      {t("datasource.description.fieldNotInDatabase")}
                    </span>
                  </div>
                  <Input
                    className="mt-1.5"
                    value={overrides[name] ?? ""}
                    onChange={(e) => setOverride(name, e.target.value)}
                    placeholder={t("datasource.description.fieldOverridePlaceholder")}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" className="px-11" onClick={() => onOpenChange(false)}>
            {t("datasource.description.cancel")}
          </Button>
          <Button type="button" className="px-11" disabled={saving} onClick={handleSave}>
            {t("datasource.description.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
