import { Dialog, DialogTrigger } from "@/components/bs-ui/dialog";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { getKnowledgeDetailApi, updateKnowledgeApi } from "@/controllers/API";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { ArrowLeft, SquarePen } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useNavigate, useLocation } from "react-router-dom";
import { Button } from "../../../components/bs-ui/button";
import ShadTooltip from "../../../components/ShadTooltipComponent";
import KnowledgeBaseSettingsDialog from "./EditKnowledgeDialog";
import Tip from "@/components/bs-ui/tooltip/tip";
import useKnowledgeStore from "../useKnowledgeStore";

interface HeaderProps {
    fileTitle: boolean;
    onBack?: () => void; // 添加回退回调
    showBackButton?: boolean; // 控制是否显示回退按钮
}

export default function Header({ fileTitle, onBack, showBackButton = true }: HeaderProps) {
    const [libInfo, setLibInfo] = useState({ name: '', desc: '' })
    // F055 (AC-09/AC-10): KB weight/priority loaded when the edit dialog opens.
    const [kbSettings, setKbSettings] = useState<{ weight: number; priority: number }>({ weight: 1, priority: 0 })
    const [open, setOpen] = useState(false)
    const { id } = useParams()
    const { t } = useTranslation()
    const navigate = useNavigate()
    const location = useLocation()

    useEffect(() => {
        // @ts-ignore
        const [libname, libdesc] = window.libname || [] // 临时记忆
        if (libname) {
            localStorage.setItem('libname', libname)
            localStorage.setItem('libdesc', libdesc)
        }
        setLibInfo({ name: libname || localStorage.getItem('libname'), desc: libdesc || localStorage.getItem('libdesc') })
    }, [])

    useEffect(() => {
        if (!open || !id) return;
        const fetchKbSettings = async () => {
            const knowledgeDetails = await captureAndAlertRequestErrorHoc(getKnowledgeDetailApi([id]));
            if (!knowledgeDetails || knowledgeDetails === 'canceled') return;
            const detail = (knowledgeDetails as Record<string, unknown>[])[0];
            if (detail) {
                setKbSettings({
                    weight: typeof detail.weight === 'number' ? detail.weight : 1,
                    priority: detail.priority === 1 ? 1 : 0,
                });
            }
        };
        void fetchKbSettings();
    }, [open, id]);

    // 默认的回退逻辑
    const handleBackDefault = () => {
        // 检查是否有历史记录可回退
        if (window.history.length > 1) {
            navigate(-1);
        } else {
            // 如果没有历史记录，回退到文件库首页
            navigate('/filelib');
        }
    }

    // 使用传入的回调或默认回调
    const handleBackClick = onBack || handleBackDefault;

    const { message } = useToast()
    const handleSave = (form) => {
        captureAndAlertRequestErrorHoc(updateKnowledgeApi({
            knowledge_id: Number(id),
            name: form.name,
            description: form.desc,
            // F055: weight 0 = excluded from multi-KB recall; priority 1 = high group
            weight: Number(form.weight),
            priority: Number(form.priority),
        })).then((res) => {
            if (!res) return
            // api
            setLibInfo(form)
            setKbSettings({ weight: Number(form.weight), priority: Number(form.priority) })
            setOpen(false)
            message({ variant: 'success', description: t('saved') })
            localStorage.setItem('libname', form.name)
            localStorage.setItem('libdesc', form.desc)
        })
    }

    const { isEditable } = useKnowledgeStore();

    return (
        <div className="flex items-start h-14 z-30">
            {/* 回退按钮 - 根据 showBackButton 控制显示 */}
            {showBackButton && (
                <ShadTooltip content={t('back')} side="top">
                    <button
                        className="extra-side-bar-buttons w-[36px]"
                        onClick={handleBackClick}
                    >
                        <ArrowLeft className="side-bar-button-size" />
                    </button>
                </ShadTooltip>
            )}

            <div>
                <div className="group flex items-center">
                    {fileTitle && (
                        <span className="text-foreground text-sm font-black pl-4 truncate max-w-80">
                            {libInfo.name}
                        </span>
                    )}
                    {/* KB settings — name/desc + F055 weight/priority */}
                    {isEditable ? <Dialog open={open} onOpenChange={setOpen}>
                        <DialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="ml-2 group-hover:visible invisible disabled:pointer-events-auto">
                                <SquarePen className="w-4 h-4" />
                            </Button>
                        </DialogTrigger>
                        {
                            open && <KnowledgeBaseSettingsDialog
                                initialName={libInfo.name}
                                initialDesc={libInfo.desc}
                                initialWeight={kbSettings.weight}
                                initialPriority={kbSettings.priority}
                                onSave={handleSave}
                            />
                        }
                    </Dialog> : null}
                </div>
            </div>
        </div>
    )
}