import type { TFunction } from "i18next"
import { describe, expect, it } from "vitest"
import { formatScheduleDisplay } from "@/components/bs-comp/apiComponent/scheduledTask/formatScheduleDisplay"
import type { ScheduleDisplay } from "@/types/api/scheduledTask"

// English fixture keeps the test free of hardcoded Chinese (lint-enforced);
// the same composition logic renders all three locales.
const EN: Record<string, string> = {
    "schedule.listSeparator": ", ",
    "schedule.weekdayRange": "{{start}}–{{end}}",
    "schedule.weekday.mon": "Mon",
    "schedule.weekday.fri": "Fri",
    "schedule.day.everyDay": "Every day",
    "schedule.day.weekdays": "Every {{weekdays}}",
    "schedule.day.monthDays": "Day {{days}} of each month",
    "schedule.day.or": "{{first}} or {{second}}",
    "schedule.time.everyMinute": "Every minute",
    "schedule.time.everyNMinutes": "Every {{n}} minutes",
    "schedule.raw": "Minute {{minute}}; hour {{hour}}; day {{dayOfMonth}}; month {{monthOfYear}}; weekday {{dayOfWeek}}",
}

const t = ((key: string, options?: Record<string, unknown>) => {
    const template = EN[key] ?? key
    return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ""))
}) as unknown as TFunction

describe("formatScheduleDisplay", () => {
    it("renders a daily time", () => {
        const display: ScheduleDisplay = {
            day: { kind: "every_day" },
            time: { kind: "at", times: [{ hour: 9, minute: 0 }] },
        }
        expect(formatScheduleDisplay(t, "en-US", display, "fallback")).toBe("Every day 9:00")
    })

    it("renders a weekday range", () => {
        const display: ScheduleDisplay = {
            day: { kind: "weekdays", weekdays: [1, 2, 3, 4, 5], range: true },
            time: { kind: "at", times: [{ hour: 9, minute: 0 }] },
        }
        expect(formatScheduleDisplay(t, "en-US", display, "fallback")).toBe("Every Mon–Fri 9:00")
    })

    it("omits the day when the time expression already carries it", () => {
        const display: ScheduleDisplay = { time: { kind: "every_n_minutes", n: 5 } }
        expect(formatScheduleDisplay(t, "en-US", display, "fallback")).toBe("Every 5 minutes")
    })

    it("renders day/time OR", () => {
        const display: ScheduleDisplay = {
            day: {
                kind: "or",
                parts: [
                    { kind: "month_days", days: [18] },
                    { kind: "weekdays", weekdays: [5], range: false },
                ],
            },
            time: { kind: "at", times: [{ hour: 9, minute: 0 }] },
        }
        expect(formatScheduleDisplay(t, "en-US", display, "fallback")).toBe(
            "Day 18 of each month or Every Fri 9:00",
        )
    })

    it("falls back to raw cron fields", () => {
        const display: ScheduleDisplay = {
            raw: { minute: "1,15", hour: "*/6", day_of_month: "*", month_of_year: "*", day_of_week: "*" },
        }
        expect(formatScheduleDisplay(t, "en-US", display, "fallback")).toBe(
            "Minute 1,15; hour */6; day *; month *; weekday *",
        )
    })

    it("returns the legacy fallback when no structured display is available", () => {
        expect(formatScheduleDisplay(t, "en-US", null, "Every minute")).toBe("Every minute")
    })
})
