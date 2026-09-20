import { Button } from "@/components/bs-ui/button";
import { Badge } from "@/components/bs-ui/badge";
import { Checkbox } from "@/components/bs-ui/checkBox";
import { Switch } from "@/components/bs-ui/switch";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/bs-ui/tooltip";
import {
  collectDatasourceTablesApi,
  listAvailableTablesApi,
  listDatasourceTablesApi,
  selectDatasourceTablesApi,
  type AvailableTableRow,
  type DatasourceTableRow,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { TableDescriptionDialog } from "./TableDescriptionDialog";

interface Props {
  datasourceId: number;
  datasourceName: string;
  onBack: () => void;
}

export function DatasourceTablesView({ datasourceId, datasourceName, onBack }: Props) {
  const { t } = useTranslation();
  const { toast } = useToast();
  // `loading` is read only by the reload button below; kept in state so a slow
  // reflection of a large schema does not look like a dead button.
  const [loading, setLoading] = useState(false);
  const [available, setAvailable] = useState<AvailableTableRow[]>([]);
  const [tables, setTables] = useState<DatasourceTableRow[]>([]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [collecting, setCollecting] = useState(false);
  const [descTarget, setDescTarget] = useState<DatasourceTableRow | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [availableRes, tablesRes] = await Promise.all([
      captureAndAlertRequestErrorHoc(listAvailableTablesApi(datasourceId)),
      captureAndAlertRequestErrorHoc(listDatasourceTablesApi(datasourceId)),
    ]);
    setLoading(false);
    if (availableRes) {
      setAvailable(availableRes.data);
      setChecked(new Set(availableRes.data.filter((row) => row.enabled).map((row) => row.table_name)));
    }
    if (tablesRes) setTables(tablesRes.data);
  }, [datasourceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSaveSelection = async () => {
    const res = await captureAndAlertRequestErrorHoc(
      selectDatasourceTablesApi(datasourceId, Array.from(checked)),
    );
    if (!res) return;
    toast({ variant: "success", title: t("datasource.tables.selectionSaved"), description: "" });
    void load();
  };

  const handleCollect = async () => {
    setCollecting(true);
    const res = await captureAndAlertRequestErrorHoc(
      collectDatasourceTablesApi(datasourceId, Array.from(checked)),
    );
    setCollecting(false);
    if (!res) return;
    toast({
      variant: "success",
      title: t("datasource.tables.collectDone", {
        created: res.created,
        refreshed: res.refreshed,
      }),
      description: "",
    });
    // A truncated collect is reported rather than swallowed: silently capping
    // reads as "your schema is complete" (backend `refresh_tables`).
    if (res.truncated) {
      toast({ variant: "error", title: t("datasource.tables.collectTruncated"), description: "" });
    }
    void load();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-3 pb-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1 size-4" />
          {t("datasource.tables.back")}
        </Button>
        <span className="font-medium">{datasourceName}</span>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>
            <RefreshCw className="mr-1 size-4" />
            {t("datasource.tables.reload")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={collecting}
            onClick={handleCollect}
          >
            {collecting ? t("datasource.tables.collecting") : t("datasource.tables.collect")}
          </Button>
          <Button size="sm" onClick={handleSaveSelection}>
            {t("datasource.tables.saveSelection")}
          </Button>
        </div>
      </div>

      {/* AC-12 is promised here, not silently relied on: the button calls
          `refresh_tables`, whose only write is the snapshot update, so an
          admin who has just spent an hour writing descriptions can see that
          pressing it is safe. */}
      <p className="shrink-0 pb-3 text-xs text-muted-foreground">
        {t("datasource.tables.collectHint")}
      </p>

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="flex w-[280px] min-w-[220px] flex-col rounded-md border">
          <div className="shrink-0 border-b px-3 py-2 text-sm font-medium">
            {t("datasource.tables.availableTitle", { count: available.length })}
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {available.length === 0 && !loading && (
              <p className="py-6 text-center text-xs text-muted-foreground">
                {t("datasource.tables.availableEmpty")}
              </p>
            )}
            {available.map((row) => (
              <label
                key={row.table_name}
                className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-accent"
              >
                <Checkbox
                  checked={checked.has(row.table_name)}
                  onCheckedChange={(value) => {
                    setChecked((prev) => {
                      const next = new Set(prev);
                      if (value === true) next.add(row.table_name);
                      else next.delete(row.table_name);
                      return next;
                    });
                  }}
                />
                <span className="min-w-0 flex-1 truncate" title={row.table_name}>
                  {row.table_name}
                </span>
                {!row.collected && (
                  <Badge variant="gray">{t("datasource.tables.notCollected")}</Badge>
                )}
              </label>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto rounded-md border">
          <div className="sticky top-0 z-10 border-b bg-background px-3 py-2 text-sm font-medium">
            {t("datasource.tables.collectedTitle", { count: tables.length })}
          </div>
          {tables.map((table) => (
            <div key={table.id} className="border-b last:border-b-0">
              <div className="flex items-center gap-2 px-3 py-2">
                <span className="font-medium">{table.table_name}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {table.table_comment || t("datasource.tables.noTableComment")}
                </span>
                {/* The exposed flag is `enabled`; unticking sets it to 0 rather
                    than deleting, which is what keeps the description alive. */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <Switch
                        checked={table.enabled === 1}
                        onCheckedChange={(value) => {
                          // The endpoint takes the whole exposed set, so one
                          // toggle sends the current set with this table added
                          // or removed — an unsaved checkbox selection is
                          // included rather than discarded.
                          const next = new Set(checked);
                          if (value) next.add(table.table_name);
                          else next.delete(table.table_name);
                          setChecked(next);
                          void captureAndAlertRequestErrorHoc(
                            selectDatasourceTablesApi(datasourceId, Array.from(next)),
                          ).then((res) => {
                            if (res) void load();
                          });
                        }}
                      />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {t("datasource.tables.exposeTooltip")}
                  </TooltipContent>
                </Tooltip>
                <Button variant="ghost" size="sm" onClick={() => setDescTarget(table)}>
                  {t("datasource.tables.editDescription")}
                </Button>
              </div>

              <div className="px-3 pb-3">
                <CollapsibleColumns
                  table={table}
                  emptyText={t("datasource.tables.columnsEmpty")}
                  headerLabels={{
                    name: t("datasource.tables.colFieldName"),
                    type: t("datasource.tables.colFieldType"),
                    comment: t("datasource.tables.colFieldComment"),
                  }}
                  commentMissingText={t("datasource.tables.commentUnavailable")}
                  businessDescLabel={t("datasource.tables.businessDesc")}
                  noBusinessDescText={t("datasource.tables.noBusinessDesc")}
                />
              </div>
            </div>
          ))}
          {tables.length === 0 && !loading && (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {t("datasource.tables.collectedEmpty")}
            </p>
          )}
        </div>
      </div>

      {descTarget && (
        <TableDescriptionDialog
          table={descTarget}
          onOpenChange={(open) => {
            if (!open) setDescTarget(null);
          }}
          onSaved={() => {
            setDescTarget(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

interface ColumnTableProps {
  table: DatasourceTableRow;
  emptyText: string;
  headerLabels: { name: string; type: string; comment: string };
  commentMissingText: string;
  businessDescLabel: string;
  noBusinessDescText: string;
}

/**
 * The collected structure: field name, type, in-database comment, plus the
 * admin-maintained business description (spec AC-11 / AC-14).
 *
 * `comment` is `undefined` when the dialect cannot report comments at all and
 * `""` when it can but the column has none. AC-14 requires the first case to be
 * stated rather than rendered as a blank cell — a blank reads as "this column
 * has no comment", which is a different and misleading claim.
 */
function CollapsibleColumns({
  table,
  emptyText,
  headerLabels,
  commentMissingText,
  businessDescLabel,
  noBusinessDescText,
}: ColumnTableProps) {
  const columns = table.structure_snapshot ?? [];
  return (
    <div className="rounded-md bg-muted/40 p-2 text-xs">
      <div className="mb-1">
        <span className="text-muted-foreground">{businessDescLabel}: </span>
        <span className={table.business_desc ? "" : "text-muted-foreground"}>
          {table.business_desc || noBusinessDescText}
        </span>
      </div>
      {columns.length === 0 ? (
        <p className="text-muted-foreground">{emptyText}</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-0.5 pr-3 font-normal">{headerLabels.name}</th>
              <th className="py-0.5 pr-3 font-normal">{headerLabels.type}</th>
              <th className="py-0.5 font-normal">{headerLabels.comment}</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column, index) => (
              <tr key={`${column.name}-${index}`} className="border-t border-border/60">
                <td className="py-0.5 pr-3">{column.name}</td>
                <td className="py-0.5 pr-3 text-muted-foreground">{column.type}</td>
                <td className="py-0.5">
                  {column.comment === undefined || column.comment === null ? (
                    <span className="italic text-amber-600">{commentMissingText}</span>
                  ) : (
                    column.comment
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
