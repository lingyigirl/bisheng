import { Button } from "@/components/bs-ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/bs-ui/table";
import { Badge } from "@/components/bs-ui/badge";
import { SearchInput } from "@/components/bs-ui/input";
import AutoPagination from "@/components/bs-ui/pagination/autoPagination";
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { PermissionDialog } from "@/components/bs-comp/permission/PermissionDialog";
import { deleteDatasourceApi, updateDatasourceApi, type DatasourceRow } from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useDebounce } from "@/util/hook";
import { Plus, Shield } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { DatasourceFormDialog } from "./DatasourceFormDialog";
import { DatasourceTablesView } from "./DatasourceTablesView";
import { engineLabelKey } from "./datasourceConstants";
import type { DatasourceQueries } from "./useDatasourceQueries";

interface Props {
  queries: DatasourceQueries;
}

export function DatasourceListTab({ queries }: Props) {
  const { t } = useTranslation();
  const { rows, total, page, pageSize, loading, setName, setPage, refetch, findRow } = queries;

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<DatasourceRow | null>(null);
  const [permissionTarget, setPermissionTarget] = useState<DatasourceRow | null>(null);

  // Debounced because the list is server-paged: without it every keystroke is
  // a request, and on a slow link the results land out of order. `handleSearch`
  // is wrapped in `useCallback` on purpose -- `useDebounce` memoises on the
  // function identity, so an inline arrow here would rebuild the debouncer each
  // render and degrade to one request per keystroke.
  const handleSearch = useCallback(
    (value: string) => {
      // Back to page 1 first: staying on page 3 while the result set shrinks
      // shows an empty table that looks like "no matches".
      setPage(1);
      setName(value);
    },
    [setPage, setName],
  );
  const debouncedSearch = useDebounce(handleSearch, 300, false);

  const handleDelete = (row: DatasourceRow) => {
    bsConfirm({
      title: `${t("datasource.list.deleteTitle")}`,
      desc: t("datasource.list.deleteConfirm", { name: row.name }),
      okTxt: t("datasource.list.delete"),
      onOk(next) {
        captureAndAlertRequestErrorHoc(deleteDatasourceApi(row.id)).then((res) => {
          if (res) void refetch();
        });
        next();
      },
    });
  };

  const handleToggleEnabled = async (row: DatasourceRow) => {
    const res = await captureAndAlertRequestErrorHoc(
      updateDatasourceApi(row.id, { enabled: row.enabled ? 0 : 1 }),
    );
    if (res) void refetch();
  };

  const editorRow = findRow(queries.tablesDatasourceId);
  if (queries.tablesDatasourceId !== null) {
    return (
      <DatasourceTablesView
        datasourceId={queries.tablesDatasourceId}
        datasourceName={editorRow?.name ?? String(queries.tablesDatasourceId)}
        onBack={() => {
          queries.setTablesDatasourceId(null);
          void refetch();
        }}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between pb-3">
        <SearchInput
          className="w-[240px]"
          defaultValue={queries.name}
          placeholder={t("datasource.list.searchNamePlaceholder")}
          onChange={(e) => debouncedSearch(e.target.value)}
        />
        <Button
          className="h-8 rounded-[6px] px-3 text-[14px]"
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus className="mr-1 size-4" />
          {t("datasource.list.create")}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("datasource.list.colName")}</TableHead>
              <TableHead>{t("datasource.list.colEngine")}</TableHead>
              <TableHead>{t("datasource.list.colAddress")}</TableHead>
              <TableHead>{t("datasource.list.colDatabase")}</TableHead>
              <TableHead>{t("datasource.list.colAccount")}</TableHead>
              <TableHead>{t("datasource.list.colStatus")}</TableHead>
              <TableHead className="text-right">{t("datasource.list.colActions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="font-medium">{row.name}</TableCell>
                <TableCell>{t(`datasource.engine.${engineLabelKey(row.engine)}`)}</TableCell>
                <TableCell className="text-muted-foreground">
                  {row.host}:{row.port}
                </TableCell>
                <TableCell>{row.database_name}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span>{row.username}</span>
                    {/* AC-05: a writable account is visible here too, not only
                        in the form — it is the one thing an admin should be
                        able to audit across all datasources at a glance. */}
                    {row.writable_account === 1 && (
                      <Badge variant="gray">{t("datasource.list.writableBadge")}</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={row.enabled ? "secondary" : "gray"}>
                    {row.enabled ? t("datasource.list.enabled") : t("datasource.list.disabled")}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => queries.setTablesDatasourceId(row.id)}
                    >
                      {t("datasource.list.tables")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditing(row);
                        setFormOpen(true);
                      }}
                    >
                      {t("datasource.list.edit")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      title={t("datasource.list.authorize")}
                      onClick={() => setPermissionTarget(row)}
                    >
                      <Shield className="size-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleToggleEnabled(row)}>
                      {row.enabled ? t("datasource.list.disable") : t("datasource.list.enable")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-red-500"
                      onClick={() => handleDelete(row)}
                    >
                      {t("datasource.list.delete")}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {!loading && rows.length === 0 && (
          <div className="py-16 text-center text-sm text-muted-foreground">
            {t("datasource.list.empty")}
          </div>
        )}
      </div>

      {/* `justify-end` is passed in rather than set by the wrapper: `Pagination`
          carries `w-full justify-center` of its own, so the wrapper's alignment
          never applies. `cname` is tailwind-merge based, so this overrides it. */}
      <div className="flex shrink-0 border-t px-2 py-2">
        <AutoPagination
          className="justify-end"
          page={page}
          pageSize={pageSize}
          total={total}
          showTotal
          onChange={setPage}
        />
      </div>

      <DatasourceFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        onSaved={() => void refetch()}
      />

      {permissionTarget && (
        <PermissionDialog
          open
          onOpenChange={(open) => {
            if (!open) setPermissionTarget(null);
          }}
          resourceType="db_datasource"
          resourceId={String(permissionTarget.id)}
          resourceName={permissionTarget.name}
        />
      )}
    </div>
  );
}
