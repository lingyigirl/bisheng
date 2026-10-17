import { useEffect, useState } from "react";
import { Button } from "~/components/ui/Button";
import { SystemMaintenanceIllustration } from "~/components/illustrations";
import { useLocalize } from "~/hooks";
import { isServiceAvailable } from "~/api/serviceHealth";

/**
 * Window event that surfaces the full-screen maintenance overlay. The axios
 * response interceptor (`~/api/request.ts`) dispatches it on any gateway/backend
 * HTTP 500 (service exception); keep the two string literals in sync.
 */
export const SERVICE_MAINTENANCE_EVENT = "bs:service-maintenance";

/**
 * Full-screen "system under maintenance" overlay shown when the backend service
 * is down (gateway returns HTTP 500). Mounted once at the app root; it stays
 * hidden until a 500 fires the event. A single, bounded probe loop clears it
 * after recovery without reloading an in-progress conversation.
 */
export function SystemMaintenanceOverlay() {
    const localize = useLocalize();
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        const show = () => setVisible(true);
        window.addEventListener(SERVICE_MAINTENANCE_EVENT, show);
        return () => window.removeEventListener(SERVICE_MAINTENANCE_EVENT, show);
    }, []);

    useEffect(() => {
        if (!visible) return;
        let disposed = false;
        let timer: ReturnType<typeof setTimeout>;
        const check = async () => {
            try {
                if (await isServiceAvailable()) {
                    if (!disposed) setVisible(false);
                    return;
                }
            } catch {
                // An outage is expected here; keep the existing recovery UI.
            }
            if (!disposed) timer = setTimeout(check, 5000);
        };
        timer = setTimeout(check, 5000);
        return () => {
            disposed = true;
            clearTimeout(timer);
        };
    }, [visible]);

    if (!visible) return null;

    return (
        <div className="fixed inset-0 z-[2000] flex flex-col items-center justify-center gap-4 bg-white px-8 text-center">
            <SystemMaintenanceIllustration className="h-[120px] w-[120px]" />
            <div className="flex flex-col items-center gap-1">
                <p className="text-base font-medium leading-6 text-text-1">
                    {localize("com_app.service_maintenance_title")}
                </p>
                <p className="text-sm leading-[22px] text-text-3">
                    {localize("com_app.service_maintenance")}
                </p>
            </div>
            <Button variant="outline" className="h-8 rounded-md px-4" onClick={() => window.location.reload()}>
                {localize("com_app.refresh")}
            </Button>
        </div>
    );
}
