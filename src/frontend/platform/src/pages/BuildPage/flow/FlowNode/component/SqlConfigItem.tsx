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

    // 定义可用的数据库选项
    const DATABASE_OPTIONS = ['MySQL', 'Db2', 'PostgreSQL', 'GaussDB', 'Oracle'];

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

                    {/* F061 AC-56：内联入口只保留对既有流程的兼容读取。
                        已填写过的流程照常显示、照常运行；但这里不再能新建连接——
                        字段全部只读，且由后端的数据源管理页承担新连接的建立。
                        这样明文密码这条债只减不增，而既有流程不受影响（AC-54）。 */}
                    <p className="mb-3 text-xs text-[#61646D]">
                        {t("sqlInlineReadonlyHint")}
                    </p>

                    {/* 数据库类型下拉框 */}
                    <Label className="flex items-center bisheng-label">{t("dbType")}</Label>
                    <Select
                        value={database_engine}
                        disabled
                        onValueChange={(value) => handleChange("database_engine", value)}
                    >
                        <SelectTrigger className={`mt-2 mb-4 nodrag opacity-60 ${errors.database_engine ? "border-red-500" : ""}`}>
                            <SelectValue placeholder={t("selectDbType")} />
                        </SelectTrigger>
                        <SelectContent>
                            {DATABASE_OPTIONS.map((engine) => (
                                <SelectItem key={engine} value={engine}>
                                    {engine}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    {/* 数据库地址 */}
                    <Label className="flex items-center bisheng-label">{t("dbAddress")}</Label> {/* 数据库地址 */}
                    <Input
                        className={`mt-2 nodrag opacity-60 ${errors.db_address ? "border-red-500" : ""}`}
                        value={db_address}
                        type="text"
                        readOnly
                        onChange={(e) => handleChange("db_address", e.target.value)}
                    />

                    {/* 数据库名称 */}
                    <Label className="flex items-center bisheng-label mt-4">{t("dbName")}</Label> {/* 数据库名称 */}
                    <Input
                        className={`mt-2 nodrag opacity-60 ${errors.db_name ? "border-red-500" : ""}`}
                        value={db_name}
                        type="text"
                        readOnly
                        onChange={(e) => handleChange("db_name", e.target.value)}
                    />

                    {/* 数据库用户名 */}
                    <Label className="flex items-center bisheng-label mt-4">{t("dbUsername")}</Label> {/* 数据库用户名 */}
                    <Input
                        className={`mt-2 nodrag opacity-60 ${errors.db_username ? "border-red-500" : ""}`}
                        value={db_username}
                        type="text"
                        readOnly
                        onChange={(e) => handleChange("db_username", e.target.value)}
                    />

                    {/* 数据库密码 */}
                    <Label className="flex items-center bisheng-label mt-4">{t("dbPassword")}</Label> {/* 数据库密码 */}
                    <Input
                        className={`mt-2 nodrag opacity-60 ${errors.db_password ? "border-red-500" : ""}`}
                        value={db_password}
                        type="password"
                        readOnly
                        onChange={(e) => handleChange("db_password", e.target.value)}
                    />
                </>
            )}
        </div>
    );
}
