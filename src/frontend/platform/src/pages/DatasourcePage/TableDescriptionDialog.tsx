import { Button } from "@/components/bs-ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/bs-ui/dialog";
import { Textarea } from "@/components/bs-ui/input";
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

/**
 * The business description an admin writes for a table (spec AC-11).
 *
 * This is the whole governance loop: a failing table shows up in the failure
 * list (AC-42), the admin lands here, and the text they write is what the model
 * is given next time. `refresh_tables` never writes this column, so a
 * re-collect cannot undo it (AC-12).
 */
export function TableDescriptionDialog({ table, onOpenChange, onSaved }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(table.business_desc ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(table.business_desc ?? "");
  }, [table]);

  const handleSave = async () => {
    setSaving(true);
    const res = await captureAndAlertRequestErrorHoc(
      updateTableDescriptionApi(table.id, value.trim()),
    );
    setSaving(false);
    if (res) onSaved();
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
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
            rows={6}
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
