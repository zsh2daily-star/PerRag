"""复查意见书导出 —— 按模板填充生成 .docx（供 router 与 MCP 共用）。

模板: templates/复查意见书-模板.docx

输入: record_id（巡查隐患记录 id）
输出: 写盘 .docx 绝对路径

聚合规则: 一份复查意见书 = 同巡查批次 + 同企业的全部隐患（序号+描述+整改状态）。
复查意见书通常在全部隐患闭环后生成，但本服务兼容部分闭环情况:
  status=closed   → "已整改"
  status=其他     → "未整改"

模板字段映射（Table 0，3 行 × 4 列）:
  P1 段落"编号：AHJ..."              → notice_number
  R0C1 企业名称（合并）               → enterprise_name
  R2C1 Para0 引言（合并）             → 替换 inspection_date + notice_number + review_date
  R2C1 Para1-N 隐患列表（合并）       → 聚合隐患 + 整改状态（每条带序号）
  R2C1 Para14 落款日期（合并）        → review_date

复用 notice_export 的公用函数（同包内复用）。
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
from app.services.svc_DocExport.notice_export import (
    DocExportError,
    _fmt_cn_date_full,
    _gather_hazards,
    _safe_filename_part,
    _set_paragraph_text,
)
from app.services.svc_DocExport.notice_export import enterprises as _enterprises_repo

# repo 单例
records = BaseRepository(InspectionRecord)
enterprises = _enterprises_repo

_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "templates", "复查意见书-模板.docx"
)
_EXPORT_DIR = os.environ.get("DOC_EXPORT_DIR_REVIEW", "/app/data/exports/复查意见书")


# ── 状态映射 ──────────────────────────────────────────────────
def _rect_status_text(status: Optional[str]) -> str:
    """巡查记录 status → 复查意见书里的整改状态文案。"""
    if status == "closed":
        return "已整改"
    return "未整改"


# ── 引言段渲染 ────────────────────────────────────────────────
def _render_intro(para: Paragraph, payload: dict) -> None:
    """R2C1 Para0 引言:重写整段，填入 inspection_date/notice_number/review_date。

    模板原文: '本网格于 2026 年8月13日作出了《安全隐患整改通知书》的决定，
              编号 AHJ032026208， 2026年   月   日经对你（单位）整改情况依法进行复查，
              情况如下：'
    重写为:   '本网格于 {insp_year} 年{insp_month}月{insp_day}日作出了《安全隐患整改
              通知书》的决定，编号 {notice_number}， {review_year}年{review_month}月
              {review_day}日经对你（单位）整改情况依法进行复查，情况如下：'
    """
    insp = payload["inspection_date"]
    rev = payload["review_date"]
    notice = payload["notice_number"]

    insp_year = insp.year if insp else ""
    insp_month = insp.month if insp else ""
    insp_day = insp.day if insp else ""
    rev_year = rev.year if rev else ""
    rev_month = rev.month if rev else ""
    rev_day = rev.day if rev else ""

    text = (
        f"本网格于 {insp_year} 年{insp_month}月{insp_day}日作出了"
        f"《安全隐患整改通知书》的决定，编号 {notice}， "
        f"{rev_year}年{rev_month}月{rev_day}日经对你（单位）整改情况依法进行复查，"
        f"情况如下："
    )
    _set_paragraph_text(para, text)


def _render_outro(cell, payload: dict) -> None:
    """R2C1 末尾落款'年  月  日' → review_date 中文全格式。

    review_date 缺失时保留模板原文'年  月  日'作占位符（配合 check_missing_fields_review
    提示用户先补全复查日期）。
    """
    review_full = _fmt_cn_date_full(payload["review_date"])
    if not review_full:
        return  # 缺失时不动模板，保留'年  月  日'占位符
    for p in cell.paragraphs:
        if p.text.strip() == "年  月  日":
            _set_paragraph_text(p, review_full)
            return


def _render_review_hazards(cell, items: list[tuple[str, str]]) -> None:
    """R2C1 隐患列表:每条带序号+整改状态，保留引言(P0)和落款。

    items: [(description, status_text), ...] 如 [("配电箱缺标志", "已整改"), ...]
    """
    paragraphs = list(cell.paragraphs)
    intro_idx = None
    outro_idx = None
    for i, p in enumerate(paragraphs):
        if "本网格于" in p.text:
            intro_idx = i
        if p.text.strip() == "年  月  日":
            outro_idx = i

    if intro_idx is None or outro_idx is None or intro_idx >= outro_idx:
        # 兜底:找不到结构,清空所有段落重建
        raise DocExportError("复查意见书模板结构异常：找不到引言或落款段")

    # 隐患段落模板:用 intro_idx+1 的段落（模板里就是第一条隐患）格式
    template_xml = deepcopy(paragraphs[intro_idx + 1]._element)

    # 删除 intro 和 outro 之间的所有段落
    for p in paragraphs[intro_idx + 1:outro_idx]:
        p._element.getparent().remove(p._element)

    # 在 intro 后顺序插入隐患段落
    insert_after = paragraphs[intro_idx]._element
    for i, (desc, status_text) in enumerate(items):
        new_xml = deepcopy(template_xml)
        # 清空 runs
        for r in new_xml.findall(qn("w:r")):
            new_xml.remove(r)
        # 去掉段落自动编号 numPr，避免与手写"1、"序号重复
        pPr = new_xml.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                pPr.remove(numPr)
        insert_after.addnext(new_xml)
        insert_after = new_xml
        new_para = Paragraph(new_xml, cell)
        sep = "。" if i == len(items) - 1 else "；"
        _set_paragraph_text(new_para, f"{i + 1}、{desc}，{status_text}{sep}")


# ── 数据获取 ──────────────────────────────────────────────────
async def _gather_record_for_review(record_id: int) -> dict:
    """聚合复查意见书所需字段。"""
    anchor = await records.get(record_id)
    if not anchor:
        raise DocExportError(f"巡查记录不存在: id={record_id}")

    rows = await _gather_hazards(anchor.inspection_batch, anchor.enterprise_id)
    if not rows:
        rows = [anchor]

    ent = await enterprises.get(anchor.enterprise_id) if anchor.enterprise_id else None
    hazard_items = [
        (r.hazard_description, _rect_status_text(r.status)) for r in rows if r.hazard_description
    ]

    return {
        "anchor": anchor,
        "rows": rows,
        "notice_number": anchor.notice_number or "",
        "enterprise_name": (ent.name if ent else "") or "",
        "inspection_date": anchor.inspection_date,
        "review_date": anchor.review_date,
        "hazard_items": hazard_items,
    }


# ── 文件名 ─────────────────────────────────────────────────────
def _build_filename(payload: dict) -> str:
    ent = _safe_filename_part(payload["enterprise_name"] or "未填企业")
    suffix = payload["notice_number"] or payload["anchor"].inspection_batch
    return f"复查意见书-{ent}-{_safe_filename_part(suffix)}.docx"


def _build_output_path(payload: dict, output_path: str) -> str:
    if output_path:
        if os.path.isdir(output_path) or output_path.endswith("/"):
            return os.path.join(output_path, _build_filename(payload))
        return output_path
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    return os.path.join(_EXPORT_DIR, _build_filename(payload))


# ── 主入口 ─────────────────────────────────────────────────────
def _fill_template(payload: dict) -> Document:
    if not os.path.exists(_TEMPLATE_PATH):
        raise DocExportError(f"复查意见书模板不存在: {_TEMPLATE_PATH}")

    doc = Document(_TEMPLATE_PATH)

    # P1 编号段落
    _render_notice_number(doc, payload["notice_number"])

    t = doc.tables[0]
    # R0C1 企业名称
    _set_paragraph_text(t.rows[0].cells[1].paragraphs[0], payload["enterprise_name"])

    # R2C1 引言 + 隐患列表 + 落款
    cell = t.rows[2].cells[1]
    # 引言 Para0
    intro_para = None
    for p in cell.paragraphs:
        if "本网格于" in p.text:
            intro_para = p
            break
    if intro_para is None:
        raise DocExportError("复查意见书模板结构异常：找不到引言段")
    _render_intro(intro_para, payload)
    _render_review_hazards(cell, payload["hazard_items"])
    _render_outro(cell, payload)

    return doc


def _render_notice_number(doc, notice_number: str) -> None:
    """P1 段落'编号：AHJ...'——把'编号：'后续 runs 合并改成实际编号。"""
    for p in doc.paragraphs:
        if "编号：" not in p.text:
            continue
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
        _set_paragraph_text(p, p.text.replace("AHJ032026208", notice_number or ""))
        return


async def export_review(record_id: int, output_path: str = "") -> str:
    """生成复查意见书 .docx，返回写盘后的绝对路径。

    参数:
        record_id: 巡查隐患记录 id（任意同批次同企业记录均可，自动聚合）
        output_path: 可选。指定文件路径或目录；不传则写入默认导出目录。

    返回: 写盘后的 .docx 绝对路径。
    异常: DocExportError —— 记录不存在或模板缺失/结构异常。
    """
    payload = await _gather_record_for_review(record_id)
    doc = _fill_template(payload)
    final_path = _build_output_path(payload, output_path)
    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
    doc.save(final_path)
    return final_path


async def check_missing_fields_review(record_id: int) -> list[str]:
    """检查生成复查意见书所需字段是否齐全，返回缺失/不满足规则原因列表（空=理想可导出）。

    前端弹窗依据：列表非空则灰化（但不禁用）"直接导出"按钮，提示用户；
    用户仍可点击强制导出（灰色=建议性禁止，非硬性拦截）。

    校验两类规则：
      字段缺失：编号/企业名/指导服务时间/复查日期/问题隐患
      导出规则：
        - 不能单条隐患导出（同批次同企业隐患数 < 2）
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
    if not r.inspection_date:
        missing.append("指导服务时间")
    if not r.review_date:
        missing.append("复查日期")

    rows = await _gather_hazards(r.inspection_batch, r.enterprise_id)
    if not any(row.hazard_description for row in rows):
        missing.append("问题隐患")
    elif len(rows) < 2:
        # 导出规则①：不能单条隐患导出
        missing.append("仅单条隐患，建议汇总后再导出")

    # 导出规则②：同批次同企业巡查日期必须一致
    dates = {row.inspection_date for row in rows if row.inspection_date}
    if len(dates) > 1:
        missing.append("巡查日期不一致")

    return missing
