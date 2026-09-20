import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DatasourceListTab } from "./DatasourceListTab";
import { QueryFailuresTab } from "./QueryFailuresTab";
import { useDatasourceQueries } from "./useDatasourceQueries";

/**
 * F061 datasource management (spec AC-57).
 *
 * Reachable at `/datasource` for a global super admin or the current tenant's
 * Child Admin — the same population `get_tenant_admin_user` admits, which is
 * what every endpoint behind this page requires.
 */
export default function DatasourcePage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("list");
  const queries = useDatasourceQueries();

  return (
    <div className="flex h-full w-full flex-col px-2 pt-4">
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 w-full flex-1 flex-col">
        <TabsList className="shrink-0 self-start">
          <TabsTrigger value="list">{t("datasource.tab.list")}</TabsTrigger>
          <TabsTrigger value="failures">{t("datasource.tab.failures")}</TabsTrigger>
        </TabsList>

        <TabsContent value="list" className="mt-3 min-h-0 flex-1">
          <DatasourceListTab queries={queries} />
        </TabsContent>

        <TabsContent value="failures" className="mt-3 min-h-0 flex-1">
          {/* The failure list is a work queue, not a report: its whole purpose
              is sending the admin to the table description that needs writing
              (AC-42), so a row click lands on that datasource's table editor.
              The target lives in the shared `queries` state rather than in a
              prop, so a second jump works — a prop would only fire on the
              initial mount and a repeat click would look like a dead row. */}
          <QueryFailuresTab
            onOpenDatasource={(datasourceId) => {
              queries.setTablesDatasourceId(datasourceId);
              setTab("list");
            }}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
