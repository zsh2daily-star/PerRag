"""文书导出端点 —— 按模板填充生成 .docx 并流式下载。

薄封装 app.services.svc_DocExport：check 接口只读库返回缺失字段，
下载接口调 service 写盘后用 FileResponse 返回 docx 附件。

路径前缀 /doc-export，共 4 个端点：
  交办单：    GET /notice/check/{id}   GET /notice/{id}
  复查意见书：GET /review/check/{id}   GET /review/{id}
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/doc-export", tags=["文书导出"])

_DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@router.get("/notice/check/{record_id}")
async def check_notice_export(record_id: int):
    """检查生成交办单所需字段是否齐全，返回缺失字段列表。

    前端弹窗据 missing 判断：非空则灰化"直接导出"按钮；空数组则允许导出。
    """
    from app.services.svc_DocExport import DocExportError, check_missing_fields

    try:
        missing = await check_missing_fields(record_id)
    except DocExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": {"missing": missing, "ready": len(missing) == 0}}


@router.get("/notice/{record_id}")
async def download_notice(record_id: int):
    """生成交办单 .docx 并作为附件流式下载。"""
    from app.services.svc_DocExport import DocExportError, export_notice

    try:
        path = await export_notice(record_id)
    except DocExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    filename = path.rsplit("/", 1)[-1]
    return FileResponse(
        path, media_type=_DOCX_MEDIA, filename=filename
    )


@router.get("/review/check/{record_id}")
async def check_review_export(record_id: int):
    """检查生成复查意见书所需字段是否齐全，返回缺失字段列表。"""
    from app.services.svc_DocExport import DocExportError, check_missing_fields_review

    try:
        missing = await check_missing_fields_review(record_id)
    except DocExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": {"missing": missing, "ready": len(missing) == 0}}


@router.get("/review/{record_id}")
async def download_review(record_id: int):
    """生成复查意见书 .docx 并作为附件流式下载。"""
    from app.services.svc_DocExport import DocExportError, export_review

    try:
        path = await export_review(record_id)
    except DocExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    filename = path.rsplit("/", 1)[-1]
    return FileResponse(
        path, media_type=_DOCX_MEDIA, filename=filename
    )
