// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { Checkbox } from "@/components/bs-ui/checkBox";
import { Input } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { getAppsApi } from "@/controllers/API/flow";
import { getAgentFlowSchemaApi } from "@/controllers/API/workflow";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { isVarInFlow } from "@/util/flowUtils";
import useFlowStore from "../../flowStore";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import SelectVar from "./SelectVar";

// F057 循环节点配置：选择要遍历的列表变量 → 选择已发布子流程作为循环体
// → 按子流程表单字段映射输入（可引用当前循环项）→ 勾选收集为列表的输出变量。
// value 结构: { loop_var: string, loop_var_label: string, item_var: string,
//               flow_id: string, max_iterations: number,
//               input_mapping: { field, mode: 'var'|'constant', value, value_label }[],
//               outputs: { key, label, ref }[] }
export default function LoopConfigItem({ nodeId, data, onChange, onOutPutChange, onValidate, onVarEvent, i18nPrefix }) {
    const { t } = useTranslation('flow');
    const { flow } = useFlowStore();          // 当前编辑中的 flow —— 选择器排除自身
    const value = data.value ?? { loop_var: '', item_var: 'item', flow_id: '', input_mapping: [], outputs: [], max_iterations: 100 };
    const [flows, setFlows] = useState([]);
    const [schema, setSchema] = useState(null);
    const [loadingSchema, setLoadingSchema] = useState(false);
    const [errorFields, setErrorFields] = useState([]);
    const [itemVarError, setItemVarError] = useState('');

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
            onChange({ ...value, flow_id: '', input_mapping: [], outputs: [] });
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
        onChange({ ...value, flow_id: flowId, input_mapping: inputMapping, outputs: [] });
        onOutPutChange('output', []);
    };

    const updateMapping = (field, patch) => {
        const next = (value?.input_mapping ?? []).map(m => (m.field === field ? { ...m, ...patch } : m));
        onChange({ ...value, input_mapping: next });
    };

    // 快捷填充：映射值引用当前循环项 <loopId>.<itemVar>
    const fillLoopItem = (field) => {
        const itemVar = value?.item_var || 'item';
        updateMapping(field, {
            mode: 'var',
            value: `${nodeId}.${itemVar}`,
            value_label: `${t('loopItemVar')}/${itemVar}`,
        });
    };

    const toggleOutput = (ref, item) => {
        const outputs = value?.outputs ?? [];
        const exists = outputs.some(o => o.ref === ref);
        const next = exists ? outputs.filter(o => o.ref !== ref) : [...outputs, { ...item, ref }];
        onChange({ ...value, outputs: next });
        // F057: handleChangeOutPut 对 loop 节点不加前缀，输出键与后端 {key: [...]} 对齐
        onOutPutChange('output', next);
    };

    // 校验 1：必须选择循环变量与要调用的流程；循环项变量名不能包含 . 或 #
    useEffect(() => {
        onValidate(() => {
            if (!value?.flow_id) return t(`${i18nPrefix}label`) + t('required');
            if (!value?.loop_var) return t(`${i18nPrefix}loopVarLabel`) + t('required');
            return false;
        });
        return () => onValidate(() => { });
    }, [value?.flow_id, value?.loop_var, i18nPrefix, t, onValidate]);

    // 校验 2：var 模式映射引用的变量必须仍存在于流程中（循环项引用除外）
    useEffect(() => {
        const validateVarAvailble = () => {
            let error = '';
            const _errorFields = [];
            const itemRef = `${nodeId}.${value?.item_var || 'item'}`;
            for (const m of value?.input_mapping ?? []) {
                if (m.mode !== 'var' || !m.value) continue;
                if (m.value === itemRef) continue;  // 当前循环项引用，无需在流程中存在
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
    }, [nodeId, flow?.nodes, value?.input_mapping, value?.item_var, onVarEvent]);

    return (
        <div className="flex flex-col gap-3">
            {/* 循环变量 */}
            <div className="flex flex-col gap-1">
                <Label className="text-sm font-medium">{t(`${i18nPrefix}loopVarLabel`)}</Label>
                <SelectVar
                    nodeId={nodeId}
                    itemKey="loop_var"
                    value={value.loop_var}
                    onSelect={(item, v) =>
                        onChange({
                            ...value,
                            loop_var: `${item.id}.${v.value}`,
                            loop_var_label: `${item.name}/${v.label}`,
                        })
                    }
                >
                    <Button variant="outline" size="sm" className="h-8 w-full truncate" type="button">
                        {value.loop_var_label || t('insertVariable')}
                    </Button>
                </SelectVar>
            </div>

            {/* 循环项变量名 */}
            <div className="flex flex-col gap-1">
                <Label className="text-sm font-medium">{t(`${i18nPrefix}itemVarLabel`)}</Label>
                <Input
                    className="h-8"
                    value={value.item_var ?? 'item'}
                    onChange={e => {
                        const v = e.target.value;
                        setItemVarError(/[.#]/.test(v) ? t(`${i18nPrefix}itemVarHelp`) : '');
                        onChange({ ...value, item_var: v });
                    }}
                    placeholder={t(`${i18nPrefix}itemVarPlaceholder`)}
                />
                {itemVarError && <span className="text-xs text-red-500">{itemVarError}</span>}
            </div>

            {/* 最大循环次数 */}
            <div className="flex flex-col gap-1">
                <Label className="text-sm font-medium">{t(`${i18nPrefix}maxIterationsLabel`)}</Label>
                <Input
                    className="h-8"
                    type="number"
                    min={1}
                    value={value.max_iterations ?? 100}
                    onChange={e => onChange({ ...value, max_iterations: Number(e.target.value) || 100 })}
                />
                <span className="text-xs text-muted-foreground">{t(`${i18nPrefix}maxIterationsHelp`)}</span>
            </div>

            {/* 选择子流程 */}
            <div className="flex flex-col gap-1">
                <Label className="text-sm font-medium">{t(`${i18nPrefix}flowLabel`)}</Label>
                <Select value={value?.flow_id || undefined} onValueChange={handleFlowSelect}>
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder={t(`${i18nPrefix}flowPlaceholder`)} />
                    </SelectTrigger>
                    <SelectContent>
                        {flows.length === 0 && <div className="px-3 py-2 text-xs text-muted-foreground">{t('loopEmpty')}</div>}
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
                        const isItemRef = mapping.mode === 'var' && mapping.value === `${nodeId}.${value?.item_var || 'item'}`;
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
                                        placeholder={t(`${i18nPrefix}constantPlaceholder`)}
                                    />
                                )}
                                <Button
                                    variant={isItemRef ? "default" : "secondary"}
                                    size="sm"
                                    className="h-8 shrink-0"
                                    type="button"
                                    onClick={() => fillLoopItem(mapping.field)}
                                >
                                    {t(`${i18nPrefix}useItem`)}
                                </Button>
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
                <span className="text-xs text-muted-foreground">{t('loopOutputEmpty')}</span>
            )}
            {selectedFlow && !schema && !loadingSchema && (
                <span className="text-xs text-muted-foreground">{t('loopSchemaUnavailable')}</span>
            )}
        </div>
    );
}
