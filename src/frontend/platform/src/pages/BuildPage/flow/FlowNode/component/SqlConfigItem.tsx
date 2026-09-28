// @ts-strict-ignore
import { Input } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { Switch } from "@/components/bs-ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { listAvailableDatasourcesApi } from "@/controllers/API/datasource";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export default function SqlConfigItem({ data, onChange, onValidate }) {
    const { t } = useTranslation('flow');
    const [values, setValues] = useState(data.value);
    const [errors, setErrors] = useState({});
    // F061 AC-53: the datasources THIS user may pick. Loaded lazily — the list
    // is only useful when the block is switched on, and every other node in the
    // flow would otherwise pay for the request.
    const [datasources, setDatasources] = useState([]);

    const { database_engine, db_address, db_name, db_username, db_password, open, datasource_id } = values;

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        captureAndAlertRequestErrorHoc(listAvailableDatasourcesApi()).then((res) => {
            if (cancelled || !res) return;
            setDatasources(res.data || []);
        });
        return () => {
            cancelled = true;
        };
    }, [open]);

    // AC-53: a bound node's engine comes off the datasource row, not off the
    // node's dead inline copy — the two can disagree, and the datasource wins.
    const boundDatasource = datasources.find((one) => one.id === datasource_id);

    // F061 T-42: labels for the READ-ONLY engine display, keyed LOWERCASE.
    // The backend stores the lowercase form (`SqlAgentParams` defaults to
    // "mysql" and its validator lowercases), so the old capitalised option list
    // could never match a stored value — Radix Select resolved it to no item and
    // rendered an empty box, which is what every node carrying an engine showed.
    // `postgres` is the pre-F061 spelling old flows still carry (AC-54).
    const ENGINE_LABELS = {
        mysql: 'MySQL',
        db2: 'Db2',
        postgresql: 'PostgreSQL',
        postgres: 'PostgreSQL',
        gaussdb: 'GaussDB',
        oracle: 'Oracle',
        dm: 'DM8',
    };
    const engineLabel = (engine) => ENGINE_LABELS[engine] || engine || '-';

    // 初始化时设置默认数据库类型
    useEffect(() => {
        if (!database_engine) {
            handleChange("database_engine", "mysql");
        }
    }, []);

    // 校验方法
    const handleValidate = () => {
        if (!open) return; // 开关关闭时无需校验

        // F061 AC-53: a bound datasource carries the connection itself, so the
        // inline fields are not required then — and must not be, or a flow that
        // picks a datasource could never be saved. Mirrors the backend's
        // `_require_a_source`, which enforces the same rule on save.
        if (datasource_id) return false;

        const newErrors = {};
        const errorMessages = [];

        const validations = [
            {
                key: "database_engine",
                value: database_engine,
                requiredMsg: t("dbTypeRequired"),
            },
            {
                key: "db_address",
                value: db_address,
                max: 200,
                requiredMsg: t("dbAddressRequired"), // 数据库地址不可为空
                maxMsg: t("dbAddressTooLong"), // 数据库地址最多 200 字
            },
            {
                key: "db_name",
                value: db_name,
                max: 100,
                requiredMsg: t("dbNameRequired"), // 数据库名称不可为空
                maxMsg: t("dbNameTooLong"), // 数据库名称最多 100 字
            },
            {
                key: "db_username",
                value: db_username,
                max: 100,
                requiredMsg: t("dbUsernameRequired"), // 数据库用户名不可为空
                maxMsg: t("dbUsernameTooLong"), // 数据库用户名最多 100 字
            },
            {
                key: "db_password",
                value: db_password,
                requiredMsg: t("dbPasswordRequired"), // 数据库密码不可为空
            },
        ];

        validations.forEach(({ key, value, max, requiredMsg, maxMsg }) => {
            if (!value) {
                newErrors[key] = true;
                errorMessages.push(requiredMsg);
            } else if (max && value.length > max) {
                newErrors[key] = true;
                errorMessages.push(maxMsg);
            }
        });

        setErrors(newErrors);
        return errorMessages.length > 0 ? errorMessages[0] : false;
    };

    // 提供校验回调
    useEffect(() => {
        onValidate(handleValidate);
        return () => onValidate(() => { });
    }, [values, data.required]);

    const handleChange = (key, value) => {
        const newValues = { ...values, [key]: value };
        setValues(newValues);
        setErrors((prev) => ({ ...prev, [key]: false })); // 清除错误状态
        onChange(newValues);
    };

    return (
        <div className="node-item mb-4 relative" data-key={data.key}>
            {/* 开关 */}
            <Switch
                className="absolute -top-8 right-2"
                checked={open}
                onCheckedChange={(checked) => handleChange("open", checked)}
            />

            {/* 配置表单 */}
            {open && (
                <>
                    {/* F061 AC-53：选一个已建数据源。这是本特性的新入口——
                        凭据是 Fernet 密文、暴露表有治理、每次调用判权。
                        只列出**当前用户有权使用**的数据源（AC-49）。 */}
                    <Label className="flex items-center bisheng-label">{t("datasourceBinding")}</Label>
                    <Select
                        value={datasource_id ? String(datasource_id) : ''}
                        onValueChange={(value) => handleChange("datasource_id", value ? Number(value) : null)}
                    >
                        <SelectTrigger className="mt-2 mb-4 nodrag">
                            <SelectValue placeholder={t("datasourceBindingPlaceholder")} />
                        </SelectTrigger>
                        <SelectContent>
                            {datasources.map((one) => (
                                <SelectItem key={one.id} value={String(one.id)}>
                                    {one.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    {/* 下面两个分支二选一：绑定态只回显数据源，未绑定态才展示内联块。 */}
                    <p className="mb-3 text-xs text-[#61646D]">
                        {t("sqlInlineReadonlyHint")}
                    </p>

                    {datasource_id ? (
                        /* F061 AC-53：绑定态只回显该数据源自己的引擎，见上。
                           地址/账号/密码**不可显示**——它们只存在于数据源行上，
                           `available-datasources` 刻意只返回 id/name/engine（INV-19 ①）。
                           此前这里照旧渲染五个内联空框，看起来像"没加载出来"。 */
                        <>
                            <Label className="flex items-center bisheng-label">{t("dbType")}</Label>
                            <Input
                                className="mt-2 nodrag opacity-60"
                                value={engineLabel(boundDatasource && boundDatasource.engine)}
                                type="text"
                                readOnly
                            />
                        </>
                    ) : (
                        /* F061 AC-56：内联入口只保留对既有流程的兼容读取——
                           已填写过的流程照常显示、照常运行；这里不再能新建连接，
                           故五个字段全部只读。密钥这条债只减不增，既有流程不受影响（AC-54）。 */
                        <>
                            {/* 数据库类型 */}
                            <Label className="flex items-center bisheng-label">{t("dbType")}</Label>
                            <Input
                                className={`mt-2 nodrag opacity-60 ${errors.database_engine ? "border-red-500" : ""}`}
                                value={engineLabel(database_engine)}
                                type="text"
                                readOnly
                            />

                            {/* 数据库地址 */}
                            <Label className="flex items-center bisheng-label mt-4">{t("dbAddress")}</Label>
                            <Input
                                className={`mt-2 nodrag opacity-60 ${errors.db_address ? "border-red-500" : ""}`}
                                value={db_address}
                                type="text"
                                readOnly
                                onChange={(e) => handleChange("db_address", e.target.value)}
                            />

                            {/* 数据库名称 */}
                            <Label className="flex items-center bisheng-label mt-4">{t("dbName")}</Label>
                            <Input
                                className={`mt-2 nodrag opacity-60 ${errors.db_name ? "border-red-500" : ""}`}
                                value={db_name}
                                type="text"
                                readOnly
                                onChange={(e) => handleChange("db_name", e.target.value)}
                            />

                            {/* 数据库用户名 */}
                            <Label className="flex items-center bisheng-label mt-4">{t("dbUsername")}</Label>
                            <Input
                                className={`mt-2 nodrag opacity-60 ${errors.db_username ? "border-red-500" : ""}`}
                                value={db_username}
                                type="text"
                                readOnly
                                onChange={(e) => handleChange("db_username", e.target.value)}
                            />

                            {/* 数据库密码 */}
                            <Label className="flex items-center bisheng-label mt-4">{t("dbPassword")}</Label>
                            <Input
                                className={`mt-2 nodrag opacity-60 ${errors.db_password ? "border-red-500" : ""}`}
                                value={db_password}
                                type="password"
                                readOnly
                                onChange={(e) => handleChange("db_password", e.target.value)}
                            />
                        </>
                    )}
                </>
            )}
        </div>
    );
}
