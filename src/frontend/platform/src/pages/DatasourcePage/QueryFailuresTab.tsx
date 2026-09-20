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
import { Input } from "@/components/bs-ui/input";
import AutoPagination from "@/components/bs-ui/pagination/autoPagination";
import {
  listQueryFailuresApi,
  type QueryFailureRow,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE = 20;

interface Props {
  /** Jump to that datasource's table editor, where the fix is written. */
  onOpenDatasource: (datasourceId: number) => void;
}

/**
 * Failed questions an admin can act on (spec AC-42).
 *
 * The rows are a work queue, not a log viewer: the point of listing the TABLES
 * a query touched is that "the model keeps guessing wrong about `orders`" is an
 * answerable signal, while a raw driver message is not. That is why each row
 * links to the description editor rather than to an error detail page.
 *
 * The user's question is deliberately absent — it is conversation text, and
 * INV-19 §3 keeps this table to statements and summaries (the backend does not
 * even select it).
 */
export function QueryFailuresTab({ onOpenDatasource }: Props) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<QueryFailureRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState("");

  const refetch = useCallback(async () => {
    const res = await captureAndAlertRequestErrorHoc(
      listQueryFailuresApi({
        page,
        page_size: PAGE_SIZE,
        error_category: category.trim() === "" ? undefined : category.trim(),
      }),
    );
    if (!res) return;
    setRows(res.data);
    setTotal(res.total);
  }, [page, category]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-3 pb-3">
        <Input
          className="max-w-[240px]"
          value={category}
          placeholder={t("datasource.failures.filterCategory")}
          onChange={(e) => {
            setPage(1);
            setCategory(e.target.value);
          }}
        />
        <span className="text-sm text-muted-foreground">
          {t("datasource.failures.total", { total })}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("datasource.failures.colTables")}</TableHead>
              <TableHead>{t("datasource.failures.colStatements")}</TableHead>
              <TableHead>{t("datasource.failures.colCategory")}</TableHead>
              <TableHead>{t("datasource.failures.colTime")}</TableHead>
              <TableHead className="text-right">{t("datasource.failures.colActions")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="align-top">
                  <div className="flex flex-wrap gap-1">
                    {row.tables.length === 0 ? (
                      <span className="text-muted-foreground">
                        {t("datasource.failures.noTables")}
                      </span>
                    ) : (
                      row.tables.map((table) => (
                        <Badge key={table} variant="gray">
                          {table}
                        </Badge>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell className="max-w-[420px] align-top">
                  {/* Every attempt, not just the last: seeing what the model
                      tried is what makes the failure diagnosable. */}
                  {row.statements.map((statement, index) => (
                    <pre
                      key={index}
                      className="mb-1 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/50 p-1 text-xs"
                    >
                      {statement}
                    </pre>
                  ))}
                </TableCell>
                <TableCell className="align-top">
                  <Badge variant="gray">{row.error_category}</Badge>
                </TableCell>
                <TableCell className="align-top text-muted-foreground">
                  {row.create_time}
                </TableCell>
                <TableCell className="text-right align-top">
                  <Button variant="ghost" size="sm" onClick={() => onOpenDatasource(row.datasource_id)}>
                    {t("datasource.failures.completeDescription")}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {rows.length === 0 && (
          <div className="py-16 text-center text-sm text-muted-foreground">
            {t("datasource.failures.empty")}
          </div>
        )}
      </div>

      <div className="flex shrink-0 justify-end border-t px-2 py-2">
        <AutoPagination page={page} pageSize={PAGE_SIZE} total={total} showTotal onChange={setPage} />
      </div>
    </div>
  );
}
