// DialogueWork.tsx
import { userContext } from "@/contexts/userContext";
import { ScopeBar } from "@/pages/ModelPage/manage/ScopeBar";
import { useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppCenter } from "./AppCenter";
import Index from "./index";
import Subscribe from "./Subscribe";
import KnowledgeSpace from "./KnowledgeSpace";
import { LLMGuardrailPolicy } from "./LLMGuardrailPolicy";
import { LLMGuardrailHitLogs } from "./LLMGuardrailHitLogs";
import { FeedbackEventLogs } from "./FeedbackEventLogs";
import { PlatformSensitiveWords } from "./PlatformSensitiveWords";
import { DocExtractSection } from "./DocExtractSection";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs";

export default function DialogueWork() {
  const [defaultValue] = useState("client");
  const [scopeVersion, setScopeVersion] = useState(0);
  const { t, i18n } = useTranslation();
  const { user } = useContext(userContext) as any;
  useEffect(() => {
    i18n.loadNamespaces('tool');
  }, [i18n]);

  // F058 (spec AC-36): the doc-extraction tab manages tenant-level config, which
  // the backend gates behind `get_tenant_admin_user` — i.e. platform super admin
  // or Child Admin. `is_department_admin` is deliberately NOT included: a
  // department admin would see the tab and then get 403s from every call in it.
  // Same pattern as SystemPage's tab gating; there is no tab-level permission
  // primitive in `web_menu` (that governs routes, not tabs).
  const showDocExtractTab =
    user?.role === "admin" || Boolean(user?.is_child_admin);

  return (
    <div className="w-full h-full px-2 pt-4 relative">
      <Tabs defaultValue={defaultValue} className="w-full mb-[40px]">
        <div className="mb-4 flex items-center gap-3">
          <ScopeBar
            user={user}
            onScopeChange={() => {
              setScopeVersion((value) => value + 1);
            }}
          />
          {/* F035 (PRD §4.8): the 灵思 tab is merged into 首页 (home) — task-mode
              display name / input placeholder live there now, the entry toggle
              moved to role menus, tools share the home pool, and the SOP
              manual library is replaced by skill management. The app-center
              copy moved out into its own 应用 tab. */}
          <TabsList className="">
            <TabsTrigger value="client">{t('bench.home')}</TabsTrigger>
            <TabsTrigger value="knowledgeSpace">{t('bench.knowledgeSpace')}</TabsTrigger>
            <TabsTrigger value="subscribe">{t('bench.subscribe')}</TabsTrigger>
            <TabsTrigger value="appCenter">{t('bench.appCenter')}</TabsTrigger>
            <TabsTrigger value="guardrail">{t('bench.guardrail')}</TabsTrigger>
            {showDocExtractTab && (
              <TabsTrigger value="docExtract">{t('docExtract.tab')}</TabsTrigger>
            )}
          </TabsList>
        </div>
        <TabsContent value="client" key="client-tab">
          <Index scopeVersion={scopeVersion} />
        </TabsContent>
        <TabsContent value="knowledgeSpace">
          <KnowledgeSpace scopeVersion={scopeVersion} />
        </TabsContent>
        <TabsContent value="subscribe">
          <Subscribe scopeVersion={scopeVersion} />
        </TabsContent>
        <TabsContent value="appCenter">
          <AppCenter scopeVersion={scopeVersion} />
        </TabsContent>
        <TabsContent value="guardrail">
          {/* F059: the platform-wide library sits above the tenant policy. Only a
              global super admin sees this tab's platform sections; the endpoints
              behind them reject everyone else. */}
          <PlatformSensitiveWords />
          <LLMGuardrailPolicy />
          <LLMGuardrailHitLogs />
          <FeedbackEventLogs />
        </TabsContent>
        {showDocExtractTab && (
          <TabsContent value="docExtract">
            <DocExtractSection />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
