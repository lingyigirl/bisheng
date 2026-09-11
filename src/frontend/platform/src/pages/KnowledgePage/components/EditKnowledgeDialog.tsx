// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const WEIGHT_MIN = 0;
const WEIGHT_MAX = 10;

export default function KnowledgeBaseSettingsDialog({
    initialName,
    initialDesc,
    initialWeight,
    initialPriority,
    onSave,
}: {
    initialName: string;
    initialDesc: string;
    initialWeight?: number;
    initialPriority?: number;
    onSave: (data: { name: string; desc: string; weight: number; priority: number }) => void;
}) {
    const { t } = useTranslation('knowledge');

    // State for form fields
    const [formData, setFormData] = useState({
        name: '',
        desc: '',
        weight: 1,
        priority: 0,
    });
    const [errors, setErrors] = useState<Record<string, string>>({});

    useEffect(() => {
        setFormData({
            name: initialName,
            desc: initialDesc,
            // F055 (AC-09/AC-10): KB weight/priority — weight 0 excludes the KB
            // from multi-KB recall, priority 1 is consulted first.
            weight: typeof initialWeight === 'number' ? initialWeight : 1,
            priority: initialPriority === 1 ? 1 : 0,
        });
    }, [initialName, initialDesc, initialWeight, initialPriority]);

    // Handle field change
    const handleChange = (e) => {
        const { name, value } = e.target;
        setFormData(prev => ({ ...prev, [name]: value }));
    };

    // Handle weight change (number input with bounds)
    const handleWeightChange = (e) => {
        const raw = e.target.value;
        const value = raw === '' ? 0 : Number(raw);
        if (Number.isNaN(value)) return;
        setFormData(prev => ({ ...prev, weight: Math.min(WEIGHT_MAX, Math.max(WEIGHT_MIN, value)) }));
    };

    // Validate the form
    const validate = () => {
        const newErrors = {};
        if (!formData.name) {
            newErrors.name = t('nameRequired');
        }
        return newErrors;
    };

    // Handle form submission
    const handleSubmit = (e) => {
        e.preventDefault();
        const validationErrors = validate();
        if (Object.keys(validationErrors).length > 0) {
            setErrors(validationErrors);
        } else {
            setErrors({});
            onSave(formData);
        }
    };

    return (
        <DialogContent className="sm:max-w-[625px] bg-background-login">
            <DialogHeader>
                <DialogTitle>{t('settings')}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-8 py-6">
                <div className="">
                    <label htmlFor="name" className="bisheng-label">
                        {t('name')}<span className="bisheng-tip">*</span>
                    </label>
                    <div className="flex items-center mt-2">
                        <Input
                            id="name"
                            name="name"
                            placeholder={t('namePlaceholder')}
                            maxLength={30}
                            className="flex-1"
                            value={formData.name}
                            onChange={handleChange}
                        />
                    </div>
                    {errors.name && <p className="bisheng-tip mt-1 text-red-500">{errors.name}</p>}
                </div>
                <div className="">
                    <label htmlFor="desc" className="bisheng-label">{t('desc')}</label>
                    <div className="flex items-center mt-2">
                        <Textarea
                            id="desc"
                            name="desc"
                            placeholder={t('descPlaceholder')}
                            maxLength={200}
                            className="flex-1"
                            value={formData.desc}
                            onChange={handleChange}
                        />
                    </div>
                </div>
                <div className="">
                    <label htmlFor="weight" className="bisheng-label">{t('weight')}</label>
                    <div className="flex items-center mt-2">
                        <Input
                            id="weight"
                            name="weight"
                            type="number"
                            min={WEIGHT_MIN}
                            max={WEIGHT_MAX}
                            step={0.1}
                            className="flex-1"
                            value={formData.weight}
                            onChange={handleWeightChange}
                        />
                    </div>
                    <p className="bisheng-tip mt-1">{t('weightHint')}</p>
                </div>
                <div className="">
                    <label htmlFor="priority" className="bisheng-label">{t('priority')}</label>
                    <div className="flex items-center mt-2">
                        <Select
                            value={String(formData.priority)}
                            onValueChange={(value) => setFormData(prev => ({ ...prev, priority: Number(value) }))}
                        >
                            <SelectTrigger className="flex-1">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="0">{t('priorityNormal')}</SelectItem>
                                <SelectItem value="1">{t('priorityHigh')}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </div>
            </div>
            <DialogFooter>
                <DialogClose>
                    <Button variant="outline" className="px-11" type="button">{t('cancel', {ns: 'bs'})}</Button>
                </DialogClose>
                <Button type="submit" className="px-11" onClick={handleSubmit}>{t('confirm', {ns: 'bs'})}</Button>
            </DialogFooter>
        </DialogContent>
    );
}
