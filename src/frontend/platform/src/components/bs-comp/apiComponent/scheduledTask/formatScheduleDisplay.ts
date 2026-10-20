import type { TFunction } from "i18next"
import { ScheduleDay, ScheduleDisplay, ScheduleTime } from "@/types/api/scheduledTask"
import { formatIsoDateTime } from "@/util/utils"

// Celery/cron weekday order: 0 = Sunday. Keys live in the `scheduledTask` namespace.
const WEEKDAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"] as const

function listJoin(t: TFunction, items: string[]): string {
    return items.filter(Boolean).join(t("schedule.listSeparator"))
}

function formatNumberList(t: TFunction, values: number[]): string {
    return listJoin(
        t,
        values.map((value) => String(value)),
    )
}

function formatMonths(t: TFunction, locale: string, months: number[]): string {
    // Locale-aware month names; no UI copy in source (zh/ja → "2月", en → "Feb").
    const formatter = new Intl.DateTimeFormat(locale || "en-US", { month: "short", timeZone: "UTC" })
    return listJoin(
        t,
        months.map((month) => formatter.format(new Date(Date.UTC(2020, month - 1, 1)))),
    )
}

function formatWeekdays(t: TFunction, weekdays: number[], isRange: boolean): string {
    const names = weekdays.map((weekday) => t(`schedule.weekday.${WEEKDAY_KEYS[weekday] ?? "sun"}`))
    if (isRange && names.length >= 2) {
        return t("schedule.weekdayRange", { start: names[0], end: names[names.length - 1] })
    }
    return listJoin(t, names)
}

function formatClock(time: { hour: number; minute: number }): string {
    return `${time.hour}:${String(time.minute).padStart(2, "0")}`
}

function formatDay(t: TFunction, locale: string, day: ScheduleDay): string {
    switch (day.kind) {
        case "every_day":
            return t("schedule.day.everyDay")
        case "weekdays":
            return t("schedule.day.weekdays", { weekdays: formatWeekdays(t, day.weekdays, day.range) })
        case "month_days":
            return t("schedule.day.monthDays", { days: formatNumberList(t, day.days) })
        case "yearly_days":
            return t("schedule.day.yearlyDays", {
                months: formatMonths(t, locale, day.months),
                days: formatNumberList(t, day.days),
            })
        case "yearly_weekdays":
            return t("schedule.day.yearlyWeekdays", {
                months: formatMonths(t, locale, day.months),
                weekdays: formatWeekdays(t, day.weekdays, day.range),
            })
        case "every_day_of_months":
            return t("schedule.day.everyDayOfMonths", { months: formatMonths(t, locale, day.months) })
        case "or":
            return t("schedule.day.or", {
                first: formatDay(t, locale, day.parts[0]),
                second: formatDay(t, locale, day.parts[1]),
            })
        default:
            return ""
    }
}

function formatTime(t: TFunction, time: ScheduleTime): string {
    switch (time.kind) {
        case "every_minute":
            return t("schedule.time.everyMinute")
        case "every_n_minutes":
            return t("schedule.time.everyNMinutes", { n: time.n })
        case "minutes_of_hour":
            return t("schedule.time.minutesOfHour", { minutes: formatNumberList(t, time.minutes) })
        case "every_n_hours_at_minute":
            return t("schedule.time.everyNHoursAtMinute", { n: time.n, minute: time.minute })
        case "every_n_minutes_of_hour":
            return t("schedule.time.everyNMinutesOfHour", { n: time.n, hour: time.hour })
        case "every_minute_of_hours":
            return t("schedule.time.everyMinuteOfHours", { hours: formatNumberList(t, time.hours) })
        case "at":
            return listJoin(
                t,
                time.times.map((item) => formatClock(item)),
            )
        case "once":
            return t("schedule.time.onceAt", { time: formatIsoDateTime(time.run_at) })
        default:
            return ""
    }
}

/**
 * Render the backend's `schedule_display` in the current language.
 *
 * Falls back to the legacy Chinese `schedule_description` / raw cron when the
 * structure is absent (older rows) or malformed. Never throws: the list must
 * keep rendering even if one row carries unexpected data.
 */
export function formatScheduleDisplay(
    t: TFunction,
    locale: string,
    display: ScheduleDisplay | null | undefined,
    fallback: string,
): string {
    if (!display) return fallback
    try {
        if ("raw" in display) {
            const { raw } = display
            return t("schedule.raw", {
                minute: raw.minute,
                hour: raw.hour,
                dayOfMonth: raw.day_of_month,
                monthOfYear: raw.month_of_year,
                dayOfWeek: raw.day_of_week,
            })
        }
        const time = formatTime(t, display.time)
        const day = display.day ? formatDay(t, locale, display.day) : ""
        return day ? `${day} ${time}` : time
    } catch {
        return fallback
    }
}
