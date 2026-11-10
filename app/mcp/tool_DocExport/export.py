"""文书导出 MCP 工具 —— 按模板填充生成 .docx 交付文书。

【适用场景】用户要求把某条巡查隐患记录导出为"安全生产指导服务问题隐患交办单"
            或"安全隐患整改复查意见书"。
【不适用】单纯查询/编辑台账 → 改用台账查询工具；导出为 Excel → 用 excel 工具组。

当前支持：
  交办单：
    ① doc_export_notice_check  导出前检查字段是否齐全（缺失则灰化"直接导出"按钮）
    ② doc_export_notice        生成 .docx 写盘，返回文件绝对路径
  复查意见书：
    ③ doc_export_review_check  导出前检查字段是否齐全
    ④ doc_export_review        生成 .docx 写盘，返回文件绝对路径

编排：先调 check 看缺失字段，缺失则提示用户先补全；无缺失调 export 生成文件。
聚合规则：以 record_id 为锚点，取同 inspection_batch + 同 enterprise_name 全部隐患。

交付方式：导出时指定 output_path（文件路径或目录），service 写盘后返回路径，
不返 bytes/base64（避免大文件塞进 MCP 消息流）。
"""
from __future__ import annotations

import json

from app.services.svc_DocExport import (
    DocExportError,
    check_missing_fields,
    check_missing_fields_review,
    export_notice,
    export_review,
)


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def register_doc_export_tools(mcp) -> None:
    """在给定 FastMCP 实例上注册文书导出工具。"""

    # ══════════════════════════════════════════════════════════════
    # 交办单导出
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def doc_export_notice_check(record_id: int) -> str:
        """【文书导出·交办单·①检查】检查生成交办单所需字段是否齐全，返回缺失字段列表。

        何时用：前端"导出交办单"按钮旁的弹窗——缺失非空则灰化"直接导出"按钮，
                 提示用户先补全再导出；为空数组则允许直接导出。
        输入字段：
            record_id: 巡查隐患记录 id（任意同批次同企业记录均可，锚点聚合）。
        返回字段（JSON）：
            - 全齐：{record_id, missing: [], ready: true}
            - 缺失：{record_id, missing: ["编号", "整改期限", ...], ready: false}
            - 异常：{error: ...}
        边界：只读数据库，不写盘。
        """
        try:
            missing = await check_missing_fields(record_id)
            return _dumps({
                "record_id": record_id,
                "missing": missing,
                "ready": len(missing) == 0,
            })
        except Exception as e:
            return _dumps({"error": f"{type(e).__name__}: {e}"})

    @mcp.tool()
    async def doc_export_notice(
        record_id: int,
        output_path: str = "",
    ) -> str:
        """【文书导出·交办单·②生成】按模板填充生成交办单 .docx 并写盘，返回文件路径。

        何时用：字段齐全（doc_export_notice_check 返回 ready=true）后调用。
        输入字段：
            record_id:  巴查隐患记录 id（任意同批次同企业记录均可，自动聚合）。
            output_path: 可选。指定文件路径或目录；不传则写入默认导出目录
                         （容器内 /app/data/exports/交办单/）自动命名。
        返回字段（JSON）：
            - 成功：{record_id, output_path, filename, message}
            - 失败：{error, record_id?}
        边界：写盘到 output_path 或默认目录；不返回文件内容（避免大文件塞进 MCP 流）。
        """
        try:
            saved_path = await export_notice(record_id, output_path)
            return _dumps({
                "record_id": record_id,
                "output_path": saved_path,
                "filename": saved_path.rsplit("/", 1)[-1] if "/" in saved_path else saved_path,
                "message": "导出成功",
            })
        except DocExportError as e:
            return _dumps({"error": str(e), "record_id": record_id})
        except Exception as e:
            return _dumps({"error": f"{type(e).__name__}: {e}", "record_id": record_id})

    # ══════════════════════════════════════════════════════════════
    # 复查意见书导出
    # ══════════════════════════════════════════════════════════════

    @mcp.tool()
    async def doc_export_review_check(record_id: int) -> str:
        """【文书导出·复查意见书·①检查】检查生成复查意见书所需字段是否齐全。

        何时用：前端"导出复查意见书"按钮旁的弹窗——缺失非空则灰化"直接导出"按钮，
                 提示用户先补全（尤其是复查日期）再导出；为空数组则允许直接导出。
        输入字段：
            record_id: 巡查隐患记录 id（任意同批次同企业记录均可，锚点聚合）。
        返回字段（JSON）：
            - 全齐：{record_id, missing: [], ready: true}
            - 缺失：{record_id, missing: ["编号", "复查日期", ...], ready: false}
            - 异常：{error: ...}
        边界：只读数据库，不写盘。
        """
        try:
            missing = await check_missing_fields_review(record_id)
            return _dumps({
                "record_id": record_id,
                "missing": missing,
                "ready": len(missing) == 0,
            })
        except Exception as e:
            return _dumps({"error": f"{type(e).__name__}: {e}"})

    @mcp.tool()
    async def doc_export_review(
        record_id: int,
        output_path: str = "",
    ) -> str:
        """【文书导出·复查意见书·②生成】按模板填充生成复查意见书 .docx 并写盘，返回文件路径。

        何时用：字段齐全（doc_export_review_check 返回 ready=true）后调用。
        隐患整改状态映射：status=closed → "已整改"；其他 → "未整改"。
        输入字段：
            record_id:  巡查隐患记录 id（任意同批次同企业记录均可，自动聚合）。
            output_path: 可选。指定文件路径或目录；不传则写入默认导出目录
                         （容器内 /app/data/exports/复查意见书/）自动命名。
        返回字段（JSON）：
            - 成功：{record_id, output_path, filename, message}
            - 失败：{error, record_id?}
        边界：写盘到 output_path 或默认目录；不返回文件内容（避免大文件塞进 MCP 流）。
        """
        try:
            saved_path = await export_review(record_id, output_path)
            return _dumps({
                "record_id": record_id,
                "output_path": saved_path,
                "filename": saved_path.rsplit("/", 1)[-1] if "/" in saved_path else saved_path,
                "message": "导出成功",
            })
        except DocExportError as e:
            return _dumps({"error": str(e), "record_id": record_id})
        except Exception as e:
            return _dumps({"error": f"{type(e).__name__}: {e}", "record_id": record_id})
