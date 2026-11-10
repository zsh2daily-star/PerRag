"""材料管理端点（阶段 C）：上传 / 列表 / 索引 / 范围 / 删除。"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel

router = APIRouter(prefix="/materials", tags=["材料管理"])


class MaterialScopeUpdateRequest(BaseModel):
    """材料范围更新请求（三形态互斥：公共通用 与 定向/涉及 二选一）。"""
    enterprise_codes: list[str] | None = None
    scope_industry: str | None = None
    scope_region: str | None = None


class MaterialUploadRequest(BaseModel):
    """材料上传/登记请求（JSON body，无真实文件时只登记记录）。"""
    filename: str
    file_format: str | None = None  # pdf/docx/xlsx/md
    enterprise_codes: list[str] | None = None
    scope_industry: str | None = None
    scope_region: str | None = None


@router.post("")
async def upload_material_endpoint(
    request: Request,
    file: UploadFile | None = File(default=None),
    filename: str | None = Form(default=None),
    enterprise_codes: list[str] | None = Form(default=None),
    scope_industry: str | None = Form(default=None),
    scope_region: str | None = Form(default=None),
):
    """上传文件或登记材料（生成 uid，status=pending）。

    支持两种方式：
      1. multipart/form-data：传 file 二进制 + filename，落本地文件；
      2. application/json：只传 filename 等字段，不落地文件（仅登记，后续补文件）。

    enterprise_codes / scope_industry / scope_region 决定范围形态（三选一互斥）。
    """
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.material_service import upload_material, MaterialError

    # 兼容 JSON body（无文件）
    if file is None and filename is None:
        try:
            body = await request.json()
            filename = body.get("filename")
            fmt = body.get("file_format") or body.get("format")
            codes = body.get("enterprise_codes") or []
            scope_industry = body.get("scope_industry")
            scope_region = body.get("scope_region")
        except Exception:
            raise HTTPException(status_code=400, detail="请求体无效")
        file_data = None
        storage_path = None
    else:
        if not filename:
            raise HTTPException(status_code=400, detail="filename 必填")
        file_data = await file.read() if file else None
        fmt = None
        codes = []
        if enterprise_codes:
            for c in enterprise_codes:
                codes.extend([x.strip() for x in c.split(",") if x.strip()])
        storage_path = None  # upload_material 内部处理

    if not filename:
        raise HTTPException(status_code=400, detail="filename 必填")

    try:
        material = await upload_material(
            filename=filename,
            file_data=file_data or b"",
            enterprise_codes=codes,
            scope_industry=scope_industry,
            scope_region=scope_region,
            uploaded_by=getattr(request.state, "user", None) and request.state.user.id,
            source_format=fmt,
        )
    except MaterialError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action_async(
        action="material.upload",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="material",
        resource_id=material.uid,
        request_payload={"filename": filename},
    )
    return {
        "success": True,
        "data": {
            "uid": material.uid,
            "filename": material.filename,
            "status": material.status,
            "file_hash": material.file_hash,
        },
    }


@router.get("")
async def list_materials_endpoint(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    status: str = "",
):
    """材料列表（分页 + 关键词/状态筛选，V2 字段：uid/enterprise_codes/scope_*）。"""
    from app.services.svc_Ledger.material_service import list_materials

    items, total = await list_materials(
        page=page, page_size=page_size,
        keyword=keyword or None, status=status or None,
    )
    return {"success": True, "data": {"total": total, "items": items}}


@router.get("/{uid}")
async def get_material_endpoint(uid: str):
    """材料详情（按 uid）。"""
    from app.services.svc_Ledger.material_service import get_by_uid

    m = await get_by_uid(uid)
    if not m:
        return {"success": False, "data": None, "message": "材料不存在"}
    return {"success": True, "data": m}


@router.post("/{uid}/index")
async def index_material_endpoint(uid: str, background_tasks: BackgroundTasks, request: Request):
    """触发材料索引（按 uid，异步后台执行）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.material_service import index_material, MaterialError

    # 先校验存在
    from app.services.svc_Ledger.material_service import get_by_uid
    if not await get_by_uid(uid):
        raise HTTPException(status_code=404, detail="材料不存在")

    log_action_async(
        action="material.index",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="material",
        resource_id=uid,
    )
    background_tasks.add_task(index_material, uid)
    return {"success": True, "message": "索引任务已启动", "uid": uid}


@router.post("/{uid}/reindex")
async def reindex_material_endpoint(uid: str, background_tasks: BackgroundTasks, request: Request):
    """重建材料索引（先删旧索引再重新索引）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.material_service import get_by_uid

    if not await get_by_uid(uid):
        raise HTTPException(status_code=404, detail="材料不存在")

    log_action_async(
        action="material.reindex",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="material",
        resource_id=uid,
    )
    from app.services.svc_Ledger.material_service import reindex_material
    background_tasks.add_task(reindex_material, uid)
    return {"success": True, "message": "重建索引任务已启动", "uid": uid}


@router.patch("/{uid}/scope")
async def update_material_scope_endpoint(
    uid: str, body: MaterialScopeUpdateRequest, request: Request
):
    """更新材料范围（先改 SQL，再同步 Qdrant payload）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.material_service import update_scope, MaterialError

    try:
        m = await update_scope(
            uid,
            enterprise_codes=body.enterprise_codes,
            scope_industry=body.scope_industry,
            scope_region=body.scope_region,
        )
    except MaterialError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not m:
        return {"success": False, "data": None, "message": "材料不存在"}

    log_action_async(
        action="material.scope_update",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="material",
        resource_id=uid,
    )
    return {"success": True, "data": m}


@router.delete("/{uid}")
async def delete_material_endpoint(uid: str, request: Request):
    """删除材料：Qdrant 数据 + DB 记录 + 本地文件（按 uid）。"""
    from app.services.svc_Auth.audit_service import log_action_async
    from app.services.svc_Ledger.material_service import delete_material

    ok = await delete_material(uid)
    if not ok:
        return {"success": False, "data": None, "message": "材料不存在"}
    log_action_async(
        action="material.delete",
        staff_id=getattr(request.state, "user", None) and request.state.user.id,
        resource_type="material",
        resource_id=uid,
    )
    return {"success": True, "message": "材料已删除"}
