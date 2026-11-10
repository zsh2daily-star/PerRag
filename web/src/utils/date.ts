/**
 * 时间展示统一工具。
 *
 * 产品约定：除系统日志（操作日志）保留精确时间外，界面上的时间一律只展示到「日期」，
 * 不展示时分秒。后端返回的 datetime 通常是 ISO 串（2026-10-23T10:15:33），
 * 这里统一截断为 YYYY-MM-DD，避免各页面各写一套 slice 逻辑。
 */

/** 取日期部分：支持 ISO 串、Date、时间戳；无效/空值返回空串 */
export function fmtDate(value?: string | number | Date | null): string {
  if (value === null || value === undefined || value === "") return "";
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? "" : value.toLocaleDateString("sv-SE");
  }
  const s = String(value).trim();
  if (!s) return "";
  // 已经是纯日期（2026-10-23 / 2026/10/23）直接规范化
  const m = s.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (m) {
    const [, y, mo, d] = m;
    return `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
  }
  const dt = new Date(s);
  if (Number.isNaN(dt.getTime())) return s;
  return dt.toLocaleDateString("sv-SE");
}

/** 日期为空时的占位符 */
export function fmtDateOr(value: string | number | Date | null | undefined, fallback = "-"): string {
  return fmtDate(value) || fallback;
}
