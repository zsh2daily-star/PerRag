"""交办单文书导出 —— 按模板填充生成 .docx（供 router 与 MCP 共用）。

模板: templates/交办单-模板.docx（随 ./app 挂载进容器 /app/app/services/svc_DocExport/templates/）

输入: record_id（巡查隐患记录 id）
输出: 写盘 .docx 绝对路径

聚合规则: 一份交办单 = 同巡查批次 + 同企业的全部隐患（序号+描述列表）。
用户在列表里挑任意一条隐患点"导出"，本服务会从该行的 inspection_batch +
enterprise_name 取整批整企业的隐患聚合，所以不必关心用户具体选了哪一条。

模板字段映射（Table 0，4 行 × 4 列）：
  P2 段落"编号：AHJ..."           → notice_number
  R0C1 企业名称                    → enterprise_name
  R0C3 网格                        → region
  R1C1 服务人员                    → inspectors
  R1C3 指导服务时间                → inspection_date
  R2C1 问题隐患（合并单元格）      → 同批次同企业全部隐患聚合（序号+描述）
  R3C1 处理意见（合并单元格）Para0 → 替换整改期限日期
  R3C1 处理意见 Para6（签署日期）  → inspection_date

复用关系：inspection_service.get_record 只返回单条；本导出要聚合多条，所以
单独实现，不直接 import get_record（避免把聚合语义塞进查询 API）。
"""
from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import date
from typing import Optional

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from sqlmodel import select

from app.db.crud import BaseRepository
from app.db.database import get_session_factory
from app.db.models import Enterprise, InspectionRecord

# repo 单例
records = BaseRepository(InspectionRecord)
enterprises = BaseRepository(Enterprise)

# 模板绝对路径：容器内 /app/app/services/svc_DocExport/templates/交办单-模板.docx
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "交办单-模板.docx")

# 导出根目录：容器内 /app/data/exports/交办单（data 已挂载）
# 宿主机直跑时通过环境变量 DOC_EXPORT_DIR 覆盖。
_EXPORT_DIR = os.environ.get("DOC_EXPORT_DIR", "/app/data/exports/交办单")


class DocExportError(ValueError):
    """文书导出操作不合规。"""


# ── 日期格式化 ──────────────────────────────────────────────────
def _fmt_cn_date_full(d: Optional[date]) -> str:
    """'YYYY年M月D日'（用于签署日期、指导服务时间）。"""
    if not d:
        return ""
    return f"{d.year}年{d.month}月{d.day}日"


def _fmt_cn_date_no_day(d: Optional[date]) -> str:
    """'YYYY年M月D'（不含'日'，用于处理意见 Para0 替换'2026年9月12'）。

    模板原文'于2026年9月12日前整改完毕'——把'2026年9月12'替换为本函数的返回值，
    后面的'日前整改完毕'里的'日'自然补上整句日期。
    """
    if not d:
        return ""
    return f"{d.year}年{d.month}月{d.day}"


def _fmt_dot_date(d: Optional[date]) -> str:
    """'YYYY.MM.DD'（指导服务时间，补零，保持模板风格）。"""
    if not d:
        return ""
    return f"{d.year:04d}.{d.month:02d}.{d.day:02d}"


# ── 段落/单元格渲染 ─────────────────────────────────────────────
def _set_paragraph_text(para: Paragraph, text: str) -> None:
    """重写段落文本，保留首个 run 的格式，删除其它 runs。

    模板里同一段落的多个 run 仅是 Word 切分文本，字体/字号一致，合并到首 run
    不会丢格式。重写后段落只有一个 run，文字内容 = text。
    """
    if para.runs:
        first = para.runs[0]
        first.text = text
        for r in para.runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        para.add_run(text)


def _render_notice_number(doc, notice_number: str) -> None:
    """P2 段落'编号：AHJ...'——把'编号：'后续 runs 合并改成实际编号。

    模板把编号值拆成 run3='AHJ032026' + run4='208'，这里把 run3 改成实际编号，
    run4 置空。兜底：若 run 切分有变，退回整段文本替换。
    """
    for p in doc.paragraphs:
        if "编号：" not in p.text:
            continue
        # 找到 "编号：" 所在 run 的索引
        idx = None
        for j, r in enumerate(p.runs):
            if r.text == "编号：":
                idx = j
                break
        if idx is not None and idx + 1 < len(p.runs):
            p.runs[idx + 1].text = notice_number or ""
            for k in range(idx + 2, len(p.runs)):
                p.runs[k].text = ""
            return
        # 兜底：整段重写（保留首 run 格式）
        _set_paragraph_text(p, p.text.replace("AHJ032026208", notice_number or ""))
        return


def _render_hazard_list(cell, items: list[str]) -> None:
    """R2C1 问题隐患合并单元格：每条隐患一段落，带序号。

    保留模板首段格式作为副本，清空原 cell 所有段落，再用副本逐条新建段落。
    """
    if not cell.paragraphs:
        cell.add_paragraph("（无隐患）")
        return

    template_xml = deepcopy(cell.paragraphs[0]._element)
    # 清空 cell 下所有段落
    for p in list(cell.paragraphs):
        p._element.getparent().remove(p._element)

    if not items:
        items = ["（无隐患）"]

    for i, text in enumerate(items):
        new_xml = deepcopy(template_xml)
        # 去掉段落自动编号 numPr，避免与手写"1、"序号重复（Word 会显示"1. 1、..."）
        pPr = new_xml.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                pPr.remove(numPr)
        cell._element.append(new_xml)
        new_para = Paragraph(new_xml, cell)
        _set_paragraph_text(new_para, f"{i + 1}、{text}")


def _render_opinion(cell, payload: dict) -> None:
    """R3C1 处理意见：替换两处日期。

    Para0 长文本含'于2026年9月12日前整改完毕'→ 把'2026年9月12'替换为 rect_deadline
        （不含'日'，让后续'日前整改完毕'的'日'补全）。
    Para6 签署日期'2026年8月13日' → 替换为 inspection_date 中文全格式。
    """
    rect_no_day = payload["rect_deadline_str_no_day"]
    inspection_full = payload["inspection_date_str_full"]

    # Para0 整改期限
    para0 = cell.paragraphs[0]
    text = para0.text
    if rect_no_day:
        text = text.replace("2026年9月12", rect_no_day)
    _set_paragraph_text(para0, text)

    # Para6 签署日期（精确匹配模板原文整段）
    for p in cell.paragraphs[1:]:
        if p.text.strip() == "2026年8月13日":
            _set_paragraph_text(p, inspection_full)
            return


# ── 数据获取 ─────────────────────────────────────────────────────
async def _gather_hazards(
    inspection_batch: str,
    enterprise_id: Optional[int],
) -> list[InspectionRecord]:
    """取同批次同企业的全部隐患，按 hazard_index → id 排序。"""
    async with get_session_factory()() as s:
        stmt = select(InspectionRecord).where(
            InspectionRecord.inspection_batch == inspection_batch,
        )
        if enterprise_id:
            stmt = stmt.where(InspectionRecord.enterprise_id == enterprise_id)
        stmt = stmt.order_by(
            InspectionRecord.hazard_index.asc().nulls_last(),
            InspectionRecord.id.asc(),
        )
        rows = (await s.execute(stmt)).scalars().all()
    return rows


async def _gather_record_for_export(record_id: int) -> dict:
    """聚合导出所需的全部字段：取一条记录作为"锚点"，再聚合本批次本企业全部隐患。

    用同批次同企业任意一行的字段（notice_number/inspectors/inspection_date/region 等
    在同批次同企业内是共享的），所以取锚点行的字段即可。
    企业名来自 Enterprise 主表（enterprise_name 冗余列已删除）。
    """
    anchor = await records.get(record_id)
    if not anchor:
        raise DocExportError(f"巡查记录不存在: id={record_id}")

    ent = await enterprises.get(anchor.enterprise_id) if anchor.enterprise_id else None
    rows = await _gather_hazards(anchor.inspection_batch, anchor.enterprise_id)
    if not rows:
        # 没有同批次同企业记录（理论上 anchor 自己应该被查到），用 anchor 单条兜底
        rows = [anchor]

    # 取锚点行的共享字段（notice_number 同批次共享，企业级字段同企业共享）
    return {
        "anchor": anchor,
        "rows": rows,
        "notice_number": anchor.notice_number or "",
        "enterprise_name": (ent.name if ent else "") or "",
        "region": anchor.region or "",
        "inspectors": anchor.inspectors or "",
        "inspection_date": anchor.inspection_date,
        "rect_deadline": anchor.rect_deadline,
        "hazard_items": [r.hazard_description for r in rows if r.hazard_description],
    }


# ── 文件名 ──────────────────────────────────────────────────────
def _safe_filename_part(s: str) -> str:
    """文件名片段：去掉 Windows/POSIX 非法字符，限长 60。"""
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s)[:60].strip("_") or "X"


def _build_output_path(payload: dict, output_path: str) -> str:
    """构造输出文件路径。

    output_path 显式传入时优先用之（MCP/router 可指定目录或文件名）；
    否则自动生成：{EXPORT_DIR}/交办单-{企业名}-{编号或批次}.docx
    """
    if output_path:
        # 用户只给目录时自动补文件名
        if os.path.isdir(output_path) or output_path.endswith("/"):
            name = _build_filename(payload)
            return os.path.join(output_path, name)
        return output_path

    name = _build_filename(payload)
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, name)


def _build_filename(payload: dict) -> str:
    ent = _safe_filename_part(payload["enterprise_name"] or "未填企业")
    suffix = payload["notice_number"] or payload["anchor"].inspection_batch
    return f"交办单-{ent}-{_safe_filename_part(suffix)}.docx"


# ── 主入口 ──────────────────────────────────────────────────────
def _fill_template(payload: dict) -> "Document":
    """打开模板，填入字段，返回 Document 对象。"""
    if not os.path.exists(_TEMPLATE_PATH):
        raise DocExportError(f"交办单模板不存在: {_TEMPLATE_PATH}")

    doc = Document(_TEMPLATE_PATH)
    _render_notice_number(doc, payload["notice_number"])

    t = doc.tables[0]
    # R0C1 企业名称 / R0C3 网格 / R1C1 服务人员 / R1C3 指导服务时间
    _set_paragraph_text(t.rows[0].cells[1].paragraphs[0], payload["enterprise_name"])
    _set_paragraph_text(t.rows[0].cells[3].paragraphs[0], payload["region"])
    _set_paragraph_text(t.rows[1].cells[1].paragraphs[0], payload["inspectors"])
    _set_paragraph_text(t.rows[1].cells[3].paragraphs[0], _fmt_dot_date(payload["inspection_date"]))

    # R2C1 问题隐患（聚合，每条一段落带序号）
    _render_hazard_list(t.rows[2].cells[1], payload["hazard_items"])

    # R3C1 处理意见（替换两处日期）
    _render_opinion(t.rows[3].cells[1], {
        "rect_deadline_str_no_day": _fmt_cn_date_no_day(payload["rect_deadline"]),
        "inspection_date_str_full": _fmt_cn_date_full(payload["inspection_date"]),
    })

    return doc


async def export_notice(record_id: int, output_path: str = "") -> str:
    """生成交办单 .docx，返回写盘后的绝对路径。

    参数:
        record_id: 巡查隐患记录 id（任意同批次同企业记录均可，会自动聚合）
        output_path: 可选。指定文件路径或目录；
                     不传则写入 _EXPORT_DIR 下自动命名文件。

    返回: 写盘后的 .docx 绝对路径。
    异常: DocExportError —— 记录不存在或模板缺失。
    """
    payload = await _gather_record_for_export(record_id)
    doc = _fill_template(payload)
    final_path = _build_output_path(payload, output_path)
    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
    doc.save(final_path)
    return final_path


async def check_missing_fields(record_id: int) -> list[str]:
    """检查生成交办单所需字段是否齐全，返回缺失/不满足规则原因列表（空=理想可导出）。

    前端弹窗依据：列表非空则灰化（但不禁用）"直接导出"按钮，提示用户；
    用户仍可点击强制导出（灰色=建议性禁止，非硬性拦截）。

    校验两类规则：
      字段缺失：编号/企业名/网格/服务人员/指导服务时间/整改期限/问题隐患
      导出规则：
        - 不能单条隐患导出（同批次同企业隐患数 < 2 → 建议汇总后再导出）
        - 不能不同日期一起导出（同批次同企业 inspection_date 不一致）
    """
    r = await records.get(record_id)
    if not r:
        return ["记录不存在"]

    ent = await enterprises.get(r.enterprise_id) if r.enterprise_id else None
    ent_name = (ent.name if ent else "") or ""

    missing: list[str] = []
    if not r.notice_number:
        missing.append("编号")
    if not ent_name:
        missing.append("企业名称")
    if not r.region:
        missing.append("网格")
    if not r.inspectors:
        missing.append("服务人员")
    if not r.inspection_date:
        missing.append("指导服务时间")
    if not r.rect_deadline:
        missing.append("整改期限")

    rows = await _gather_hazards(r.inspection_batch, r.enterprise_id)
    if not any(row.hazard_description for row in rows):
        missing.append("问题隐患")
    elif len(rows) < 2:
        # 导出规则①：不能单条隐患导出（文书应是多条隐患的汇总）
        missing.append("仅单条隐患，建议汇总后再导出")

    # 导出规则②：同批次同企业巡查日期必须一致
    dates = {row.inspection_date for row in rows if row.inspection_date}
    if len(dates) > 1:
        missing.append("巡查日期不一致")

    return missing
