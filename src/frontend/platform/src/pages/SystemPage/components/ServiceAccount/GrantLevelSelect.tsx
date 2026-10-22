import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { ResourceGrantLevel } from "@/types/api/serviceAccount"
import { useTranslation } from "react-i18next"
import { GRANT_LEVELS } from "./resourceGrantLoaders"

interface GrantLevelSelectProps {
    value: ResourceGrantLevel
    onChange: (level: ResourceGrantLevel) => void
    disabled?: boolean
    className?: string
}

/** Shared level picker for the add-grant and edit-grant dialogs. */
export function GrantLevelSelect({ value, onChange, disabled = false, className }: GrantLevelSelectProps) {
    const { t } = useTranslation("serviceAccount")
    return (
        <div className={className}>
            <Label className="bisheng-label">{t("grants.dialog.level")}</Label>
            <Select value={value} onValueChange={(next) => onChange(next as ResourceGrantLevel)} disabled={disabled}>
                <SelectTrigger>
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    {GRANT_LEVELS.map((one) => (
                        <SelectItem key={one} value={one}>
                            {t(`grants.level.${one}`)}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </div>
    )
}
