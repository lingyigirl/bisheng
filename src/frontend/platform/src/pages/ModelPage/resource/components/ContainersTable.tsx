// @ts-strict-ignore
import { Badge } from "@/components/bs-ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import type { ContainerInfo } from "@/types/api/gpu";
import { useTranslation } from "react-i18next";

interface ContainersTableProps {
    containers: ContainerInfo[];
}

export function ContainersTable({ containers }: ContainersTableProps) {
    const { t } = useTranslation();
    if (!containers.length) return <div className="text-sm text-gray-400 p-4">{t("model.containersEmpty")}</div>;
    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead>{t("model.containerName")}</TableHead>
                    <TableHead>{t("model.containerImage")}</TableHead>
                    <TableHead>{t("model.containerStatus")}</TableHead>
                    <TableHead>{t("model.containerPorts")}</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {containers.map((item) => (
                    <TableRow key={item.container_id}>
                        <TableCell className="font-medium">{item.name}</TableCell>
                        <TableCell className="text-gray-500">{item.image}</TableCell>
                        <TableCell>
                            <Badge variant={item.status.startsWith("Up") ? "secondary" : "outline"}>{item.status}</Badge>
                        </TableCell>
                        <TableCell className="text-gray-500">{item.ports || "-"}</TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    );
}
