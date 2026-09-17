import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs";
import { useTranslation } from "react-i18next";

import { DocExtractRecords } from "./DocExtractRecords";
import { DocExtractTemplates } from "./DocExtractTemplates";

/**
 * Contents of the 文档抽取 tab: template management + extraction records.
 *
 * The records tab is not a separate top-level tab because it exists to answer
 * "why did this document extract wrong?", which is a question asked while
 * looking at the template that produced it.
 */
export function DocExtractSection() {
    const { t } = useTranslation();

    return (
        <Tabs defaultValue="templates" className="w-full">
            <TabsList>
                <TabsTrigger value="templates">{t("docExtract.templates", { ns: "bs" })}</TabsTrigger>
                <TabsTrigger value="records">{t("docExtract.records", { ns: "bs" })}</TabsTrigger>
            </TabsList>
            <TabsContent value="templates" className="pt-4">
                <DocExtractTemplates />
            </TabsContent>
            <TabsContent value="records" className="pt-4">
                <DocExtractRecords />
            </TabsContent>
        </Tabs>
    );
}
