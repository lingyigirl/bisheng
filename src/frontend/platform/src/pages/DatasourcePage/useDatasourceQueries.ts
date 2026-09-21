import {
  listDatasourcesApi,
  type DatasourceRow,
} from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useCallback, useEffect, useState } from "react";

const PAGE_SIZE = 20;

/**
 * The list state both tabs share.
 *
 * Hoisted to the page rather than kept inside `DatasourceListTab` so the
 * failures tab can send the admin INTO a specific datasource's table editor
 * (spec AC-42) without that tab having to re-fetch the list to resolve a name.
 *
 * Plain `useState` + effects, not react-query v3: that package is frozen for
 * new usage in this app (platform AGENTS.md).
 */
export function useDatasourceQueries() {
  const [rows, setRows] = useState<DatasourceRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  /** The name filter box. Kept here rather than in the list tab because
   *  `refetch` lives here, and two sources of truth for one request is how
   *  the filter and the reload button drift apart. */
  const [name, setName] = useState("");

  /** Which datasource the table editor is open on; null = list view. */
  const [tablesDatasourceId, setTablesDatasourceId] = useState<number | null>(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    const keyword = name.trim();
    const res = await captureAndAlertRequestErrorHoc(
      listDatasourcesApi({
        page,
        page_size: PAGE_SIZE,
        // Omitted rather than sent empty: the backend treats a blank `name` as
        // "no filter", and not sending it keeps that one rule in one place.
        ...(keyword === "" ? {} : { name: keyword }),
      }),
    );
    setLoading(false);
    if (!res) return;
    setRows(res.data);
    setTotal(res.total);
  }, [page, name]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const findRow = useCallback(
    (id: number | null) => (id === null ? null : rows.find((row) => row.id === id) ?? null),
    [rows],
  );

  return {
    rows,
    total,
    page,
    pageSize: PAGE_SIZE,
    loading,
    name,
    setName,
    setPage,
    refetch,
    findRow,
    tablesDatasourceId,
    setTablesDatasourceId,
  };
}

export type DatasourceQueries = ReturnType<typeof useDatasourceQueries>;
