// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { Checkbox } from "@/components/bs-ui/checkBox";
import { Input } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { getAppsApi } from "@/controllers/API/flow";
import { getAgentFlowSchemaApi } from "@/controllers/API/workflow";
import { isVarInFlow } from "@/util/flowUtils";
import useFlowStore from "../../flowStore";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import SelectVar from "./SelectVar";

// F056 智能体节点配置：选择要调用的已发布工作流 → 按被调流程表单字段映射输入
// （引用上游变量或固定值）→ 勾选需要暴露给下游节点的输出变量。
// value 结构: { flow_id: string,
//               input_mapping: { field, mode: 'var'|'constant', value, value_label }[],
//               outputs: { key, label, ref }[] }
export default function AgentFlowConfigItem({ nodeId, data, onChange, onOutPutChange, onValidate, onVarEvent, i18nPrefix }) {
    const { t } = useTranslation('flow');
    const { flow } = useFlowStore();          // 当前编辑中的 flow —— 选择器排除自身
    const value = data.value ?? { flow_id: '', input_mapping: [], outputs: [] };
    const [flows, setFlows] = useState([]);
    const [schema, setSchema] = useState(null);
    const [loadingSchema, setLoadingSchema] = useState(false);
    const [errorFields, setErrorFields] = useState([]);

    // 已发布 WORKFLOW 列表（排除当前 flow；游标翻页，最多 5 页）
    useEffect(() => {
        const fetchAll = async () => {
            const all = [];
            let cursor;
            for (let i = 0; i < 5; i++) {
                const res = await getAppsApi({ type: 'flow', status: 2, permissionId: 'use_app', pageSize: 100, cursor });
                all.push(...(res?.data ?? []));
                if (!res?.has_more) break;
                cursor = res?.next_cursor;
            }
            setFlows(all.filter(item => item?.id !== flow?.id));
        };
        captureAndAlertRequestErrorHoc(fetchAll());
    }, [flow?.id]);

    const selectedFlow = useMemo(() => flows.find(item => item?.id === value?.flow_id), [flows, value?.flow_id]);

    // 选中流程后拉取其输入/输出 schema，初始化映射默认值（constant + 字段默认值）
    const handleFlowSelect = async (flowId) => {
        setSchema(null);
        if (!flowId) {
            onChange({ flow_id: '', input_mapping: [], outputs: [] });
            onOutPutChange('output', []);
            return;
        }
        setLoadingSchema(true);
        const res = await captureAndAlertRequestErrorHoc(getAgentFlowSchemaApi(flowId));
        setLoadingSchema(false);
        if (!res) return;
        const schemaData = res?.data ?? res;
        const inputMapping = [];
        const seenFields = new Set();
        for (const inputNode of schemaData?.input ?? []) {
            for (const field of inputNode?.fields ?? []) {
                if (!field?.supported || seenFields.has(field.key)) continue;
                seenFields.add(field.key);
                inputMapping.push({
                    field: field.key,
                    mode: 'constant',
                    value: field.value ?? '',
                    value_label: '',
                });
            }
        }
        setSchema({ input: schemaData?.input ?? [], outputs: schemaData?.outputs ?? [] });
        onChange({ flow_id: flowId, input_mapping: inputMapping, outputs: [] });
        onOutPutChange('output', []);
    };

    const updateMapping = (field, patch) => {
        const next = (value?.input_mapping ?? []).map(m => (m.field === field ? { ...m, ...patch } : m));
        onChange({ ...value, input_mapping: next });
    };

    const toggleOutput = (ref, item) => {
        const outputs = value?.outputs ?? [];
        const exists = outputs.some(o => o.ref === ref);
        const next = exists ? outputs.filter(o => o.ref !== ref) : [...outputs, { ...item, ref }];
        onChange({ ...value, outputs: next });
        // 与 VarSelectItem 的 linkage 行为一致：handleChangeOutPut 按 output_
        // 前缀重写输出 param 的 value（与后端返回键 output_<key> 对齐）
        onOutPutChange('output', next);
    };

    // 校验 1：必须选择要调用的流程
    useEffect(() => {
        onValidate(() => {
            if (!value?.flow_id) return t(`${i18nPrefix}label`) + t('required');
            return false;
        });
        return () => onValidate(() => { });
    }, [value?.flow_id, i18nPrefix, t, onValidate]);

    // 校验 2：var 模式映射引用的上游变量必须仍存在于流程中
    useEffect(() => {
        const validateVarAvailble = () => {
            let error = '';
            const _errorFields = [];
            for (const m of value?.input_mapping ?? []) {
                if (m.mode !== 'var' || !m.value) continue;
                const varError = isVarInFlow(nodeId, flow?.nodes, m.value);
                if (varError) {
                    _errorFields.push(m.field);
                    error = varError;
                }
            }
            setErrorFields(_errorFields);
            return Promise.resolve(error);
        };
        if (onVarEvent) onVarEvent(validateVarAvailble);
        return () => {
            if (onVarEvent) onVarEvent(() => { });
        };
    }, [nodeId, flow?.nodes, value?.input_mapping, onVarEvent]);

    return (
        <div className="flex flex-col gap-3">
            {/* 选择智能体 */}
            <div className="flex flex-col gap-1">
                <Label className="text-sm font-medium">{t(`${i18nPrefix}label`)}</Label>
                <Select value={value?.flow_id || undefined} onValueChange={handleFlowSelect}>
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder={t(`${i18nPrefix}placeholder`)} />
                    </SelectTrigger>
                    <SelectContent>
                        {flows.length === 0 && <div className="px-3 py-2 text-xs text-muted-foreground">{t('agentFlowEmpty')}</div>}
                        {flows.map(item => (
                            <SelectItem key={item.id} value={item.id}>
                                {item.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {loadingSchema && <span className="text-xs text-muted-foreground">...</span>}
            </div>

            {/* 输入映射 */}
            {(value?.input_mapping ?? []).length > 0 && (
                <div className="flex flex-col gap-2 rounded-md border p-3">
                    <span className="text-xs font-medium text-muted-foreground">{t('agentFlowInputMapping')}</span>
                    {value.input_mapping.map(mapping => {
                        const fieldInfo = (schema?.input ?? [])
                            .flatMap(n => n?.fields ?? [])
                            .find(f => f?.key === mapping.field);
                        return (
                            <div key={mapping.field} className="flex items-center gap-2">
                                <span className={`w-28 shrink-0 truncate text-sm ${errorFields.includes(mapping.field) ? 'text-red-500' : ''}`} title={fieldInfo?.label ?? mapping.field}>
                                    {fieldInfo?.label ?? mapping.field}
                                </span>
                                <Select
                                    value={mapping.mode}
                                    onValueChange={(mode) => updateMapping(mapping.field, { mode })}
                                >
                                    <SelectTrigger className="w-24 shrink-0">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="constant">{t('agentFlowModeConstant')}</SelectItem>
                                        <SelectItem value="var">{t('agentFlowModeVar')}</SelectItem>
                                    </SelectContent>
                                </Select>
                                {mapping.mode === 'var' ? (
                                    <SelectVar
                                        nodeId={nodeId}
                                        itemKey={mapping.field}
                                        value={mapping.value}
                                        onSelect={(item, v) =>
                                            updateMapping(mapping.field, {
                                                value: `${item.id}.${v.value}`,
                                                value_label: `${item.name}/${v.label}`,
                                            })
                                        }
                                    >
                                        <Button variant="outline" size="sm" className="h-8 w-full truncate" type="button">
                                            {mapping.value_label || t('insertVariable')}
                                        </Button>
                                    </SelectVar>
                                ) : (
                                    <Input
                                        className="h-8"
                                        value={mapping.value ?? ''}
                                        onChange={e => updateMapping(mapping.field, { value: e.target.value })}
                                        placeholder={t('agentFlowConstantPlaceholder')}
                                    />
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {/* 输出变量 */}
            {(schema?.outputs ?? []).length > 0 && (
                <div className="flex flex-col gap-2 rounded-md border p-3">
                    <span className="text-xs font-medium text-muted-foreground">{t(`${i18nPrefix}outputLabel`)}</span>
                    {schema.outputs.map(item => (
                        <label key={item.ref} className="flex items-center gap-2 text-sm">
                            <Checkbox
                                checked={(value?.outputs ?? []).some(o => o.ref === item.ref)}
                                onCheckedChange={() => toggleOutput(item.ref, { key: item.key, label: item.label })}
                            />
                            <span className="truncate">{item.label}</span>
                            <span className="text-xs text-muted-foreground">{item.ref}</span>
                        </label>
                    ))}
                </div>
            )}
            {schema && (schema?.outputs ?? []).length === 0 && (
                <span className="text-xs text-muted-foreground">{t('agentFlowOutputEmpty')}</span>
            )}
            {selectedFlow && !schema && !loadingSchema && (
                <span className="text-xs text-muted-foreground">{t('agentFlowSchemaUnavailable')}</span>
            )}
        </div>
    );
}
