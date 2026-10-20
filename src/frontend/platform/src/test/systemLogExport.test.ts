import { describe, expect, it } from "vitest";

import {
  buildAuditExportRows,
  resolveAuditExportRange,
  sanitizeCsvCell,
} from "@/pages/LogPage/systemLog/auditLogFormat";

// 本地时区构造 Date，避免 CI 时区差异导致格式化结果漂移。
const localDate = (year: number, month: number, day: number) =>
  new Date(year, month - 1, day, 12, 0, 0);

const identityT = (key: string) => key;

describe("resolveAuditExportRange", () => {
  it("defaults to the last 60 days when neither bound is selected", () => {
    const range = resolveAuditExportRange(undefined, undefined, localDate(2026, 10, 19));

    expect(range).toEqual({
      start: "2026-08-21 00:00:00",
      end: "2026-10-19 23:59:59",
      notice: "default",
    });
  });

  it("fills a 60-day window after a start-only selection", () => {
    const range = resolveAuditExportRange(localDate(2026, 10, 1), undefined, localDate(2026, 10, 19));

    expect(range).toEqual({
      start: "2026-10-01 00:00:00",
      end: "2026-11-29 23:59:59",
      notice: "custom",
    });
  });

  it("fills a 60-day window before an end-only selection", () => {
    const range = resolveAuditExportRange(undefined, localDate(2026, 10, 19), localDate(2026, 10, 19));

    expect(range).toEqual({
      start: "2026-08-21 00:00:00",
      end: "2026-10-19 23:59:59",
      notice: "custom",
    });
  });

  it("keeps an explicit range and emits no notice", () => {
    const range = resolveAuditExportRange(localDate(2026, 1, 1), localDate(2026, 1, 31), localDate(2026, 10, 19));

    expect(range).toEqual({
      start: "2026-01-01 00:00:00",
      end: "2026-01-31 23:59:59",
      notice: null,
    });
  });
});

describe("sanitizeCsvCell", () => {
  it("prefixes formula-leading strings to prevent CSV injection", () => {
    expect(sanitizeCsvCell("=1+1")).toBe("'=1+1");
    expect(sanitizeCsvCell("+cmd")).toBe("'+cmd");
    expect(sanitizeCsvCell("-cmd")).toBe("'-cmd");
    expect(sanitizeCsvCell("@cmd")).toBe("'@cmd");
  });

  it("leaves ordinary strings and non-strings untouched", () => {
    expect(sanitizeCsvCell("admin")).toBe("admin");
    expect(sanitizeCsvCell(42)).toBe(42);
    expect(sanitizeCsvCell(undefined)).toBeUndefined();
  });
});

describe("buildAuditExportRows", () => {
  it("builds the header row and legacy audit rows in table column order", () => {
    const rows = buildAuditExportRows(
      [
        {
          id: "audit-1",
          operator_name: "admin",
          create_time: "2026-10-19T10:00:00",
          system_id: "chat",
          event_type: "create_chat",
          object_type: "assistant",
          object_name: "my-app",
          ip_address: "10.0.0.1",
          note: null,
        },
      ],
      identityT,
    );

    expect(rows[0]).toEqual([
      "log.auditId",
      "log.username",
      "log.operationTime",
      "log.systemModule",
      "log.operationAction",
      "log.objectType",
      "log.operationObject",
      "log.ipAddress",
      "log.remark",
    ]);
    expect(rows[1]).toEqual([
      "audit-1",
      "admin",
      "2026-10-19 10:00:00",
      "log.systemIdEnum.chat",
      "log.eventTypeEnum.create_chat",
      "log.objectTypeEnum.assistant",
      "my-app",
      "10.0.0.1",
      null,
    ]);
  });

  it("falls back to structured v2 fields when legacy columns are empty", () => {
    const rows = buildAuditExportRows(
      [
        {
          id: "audit-2",
          operator_name: "ops",
          create_time: "2026-10-19T11:00:00",
          system_id: null,
          event_type: null,
          object_type: null,
          action: "tenant.mount",
          target_type: null,
          target_id: "tenant-9",
          object_name: null,
          ip_address: "10.0.0.2",
          note: "=HYPERLINK()",
        },
      ],
      identityT,
    );

    expect(rows[1][3]).toBe("log.systemIdEnum.tenant");
    expect(rows[1][4]).toBe("log.eventTypeEnum.tenantMount");
    expect(rows[1][5]).toBe("log.objectTypeEnum.none");
    expect(rows[1][6]).toBe("tenant-9");
    expect(rows[1][8]).toBe("'=HYPERLINK()");
  });
});
