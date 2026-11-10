"""数据库模型定义 —— 基于 SQLModel 的业务表结构。

核心台账（V2 五表）:
    staff              监管人员（凭证 + 档案 + is_admin 合一）
    enterprise         企业（被监管对象主数据，不登录）
    material           材料登记（一行一份文件，切片在 Qdrant 不落 SQL）
    inspection_record  巡查隐患记录（一巡查多隐患，每行一条隐患+整改）
    audit_log          审计日志（只追加）

专项整治:
    factory_in_factory 厂中厂消防整治记录（出租方-承租方关系行）

智能问答（Hermes 流式代理配套）:
    chat_session       问答会话（归属 staff，Hermes 侧无状态，上下文由本表组装）
    chat_message       问答消息（role: user/assistant；工具进度只透传不落库）

设计要点:
    - 凭证与档案无需拆表，staff 一张表搞定（一人一号 1:1）
    - is_admin 是布尔位不是角色体系，仅额外放行账号管理接口
    - material 用 enterprise_codes text[] 数组弱引用企业，无外键约束
    - inspection_record 单表覆盖巡查信息 + 隐患 + 整改，按 inspection_batch 聚合

注意：所有可空字段统一用 Optional[X] = None 而非 X | None，
      以保证 SQLModel（pydantic 兼容层）能正确识别字段类型。
"""

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel


class Staff(SQLModel, table=True):
    """监管人员表 —— 凭证 + 档案 + 权限合一。

    内部系统只有"用户名+密码"，一人一号严格 1:1，凭证与档案无需拆表。
    is_admin 是布尔位不是角色体系：true 仅额外能调账号管理接口；
    业务数据全员平等（后端不按 enterprise 过滤）。
    停用不物理删除（is_active=false），历史单据姓名保留。
    """

    __tablename__ = "staff"
    id: Optional[int] = Field(default=None, primary_key=True)
    # ── 凭证 ──
    username: str = Field(unique=True, index=True)
    password_hash: str  # bcrypt
    is_active: bool = Field(default=True)
    last_login_at: Optional[datetime] = None
    # ── 权限 ──
    is_admin: bool = Field(default=False)  # 仅额外放行账号管理接口
    # ── 对话配额 ──
    chat_daily_limit_yuan: Optional[float] = Field(default=None)  # 每日费用上限（元），None=用系统默认；管理员不受限
    chat_locked: bool = Field(default=False)  # 管理员手动锁定对话功能（超限自动锁 24h 或手动锁）
    # ── 档案 ──
    name: str
    department: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class Enterprise(SQLModel, table=True):
    """企业表 —— 被监管对象主数据，不登录。

    industry/region 是类型标签（前端分组筛选 + 材料定向匹配依据）。
    industry 取固定字典（纺织/机械/建材/轻工/化工/其他），仍以中文文本存储。
    code（统一社会信用代码）是跨库稳定身份，材料数组引用它。
    高频监管属性固化为正式列（可筛选/排序）；其他长尾属性进 extra jsonb。
    删除高风险：有关联巡查单禁止删。
    """

    __tablename__ = "enterprise"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: Optional[str] = Field(default=None, unique=True, index=True)  # 统一社会信用代码（NULL=未填报，导入通道允许；手工创建仍必填）
    industry: Optional[str] = None  # 行业（固定字典：纺织/机械/建材/轻工/化工/其他）
    region: Optional[str] = None  # 区域（类型标签）
    address: Optional[str] = None
    legal_person: Optional[str] = None  # 法人（营业执照）
    person_in_charge: Optional[str] = None  # 企业负责人（实际经营管理）
    safety_officer: Optional[str] = None  # 企业安全员
    legal_person_phone: Optional[str] = None  # 法人电话
    person_in_charge_phone: Optional[str] = None  # 企业负责人电话
    safety_officer_phone: Optional[str] = None  # 安全员电话
    # ── 基础信息：企业类型/资产属性（企业类型为多标签 text[]，资产属性为单值枚举）──
    enterprise_category: Optional[List[str]] = Field(
        default=None,
        sa_column=Column("enterprise_category", ARRAY(String), nullable=True),
    )  # 企业类型多标签：劳密/厂中厂出租/厂中厂承租/小场所/其他
    asset_attribute: Optional[str] = None  # 资产属性：韩资/台资/其他
    # ── 规模信息 ──
    enterprise_scale: Optional[str] = None  # 企业规模：规上/规下
    employee_count: Optional[int] = None  # 职工人数
    annual_output_value: Optional[int] = None  # 年产值（万元）
    # ── 安全信息：消防等级（枚举）+ 各风险点 bool（界面标签不带“是否”）──
    fire_level: Optional[str] = None  # 消防重点单位：二级/三级/重点单位
    confined_space: Optional[List[str]] = Field(
        default=None,
        sa_column=Column("confined_space", ARRAY(String), nullable=True),
    )  # 有限空间多标签：地上/地下（可同时存在，导入打标签）
    # ── 有限空间明细（从 extra 迁移固化，便于筛选/排序）──
    confined_space_count: Optional[str] = None    # 有限空间数量
    confined_space_type: Optional[str] = None     # 有限空间类型（详细，如"地上、地下及封闭空间"）
    confined_space_workers: Optional[str] = None   # 有限空间作业人数
    confined_space_hazards: Optional[str] = None  # 有限空间危险因素
    confined_space_op_subject: Optional[str] = None    # 有限空间作业主体（自行/发包等）
    confined_space_op_cycle: Optional[str] = None      # 有限空间作业周期
    confined_space_fixed_workers: Optional[str] = None  # 有限空间作业人员是否固定（是/否）
    # ── 危化品明细（从 extra 迁移固化；多值以"、"拼接）──
    hazchem_names: Optional[str] = None           # 危化品名称
    hazchem_op_subject: Optional[str] = None      # 作业主体（自行/发包/自行和发包）
    hazchem_major_hazard: Optional[str] = None    # 是否重大危险源
    hazchem_state: Optional[str] = None           # 危化品状态（液态/固态/气态）
    hazchem_op_cycle: Optional[str] = None        # 作业周期（月/年）
    hazchem_usage: Optional[str] = None           # 危化品用途
    hazchem_annual_usage: Optional[str] = None    # 年度使用量
    hazchem_storage: Optional[str] = None         # 存储方式
    hazchem_reserve: Optional[str] = None         # 危化品储量
    hazchem_fixed_workers: Optional[str] = None   # 作业人员是否固定
    # ── 小场所明细（从 extra 迁移固化，便于筛选/排序；2026-10 新增专页）──
    venue_status: Optional[str] = None            # 场所状态（在业/停业）
    staff_count: Optional[str] = None             # 从业人员数（范围档位）
    venue_area: Optional[str] = None              # 场所面积（范围档位）
    capacity: Optional[str] = None               # 承载人数（范围档位）
    is_multistory: Optional[bool] = None          # 是否占用多层
    has_business_license: Optional[bool] = None  # 有无营业执照
    venue_category_major: Optional[str] = None   # 场所大类
    venue_category_minor: Optional[str] = None    # 场所小类
    is_multi_license: Optional[bool] = None      # 是否一店多证
    regulatory_dept: Optional[str] = None        # 专业监管部门及监管事项
    territorial_dept: Optional[str] = None       # 属地管理部门
    venue_safety_inspector: Optional[str] = None # 小场所安全巡查员及联系方式（专页口径）
    venue_fire_instructor: Optional[str] = None # 小场所消防指导员及联系方式（专页口径）
    venue_risk_level: Optional[str] = None       # 小场所风险等级（蓝/黄/橙/红）
    involves_evacuation: Optional[bool] = None       # 涉及疏散安全
    involves_fire_safety: Optional[bool] = None     # 涉及消防安全
    involves_electrical: Optional[bool] = None      # 涉及用电安全
    involves_lpg: Optional[bool] = None              # 涉及瓶装液化气
    involves_pipe_gas: Optional[bool] = None         # 涉及管道天然气
    involves_other_fuel: Optional[bool] = None       # 涉及其他燃料（丙烷、醇基等）
    involves_decoration_work: Optional[bool] = None  # 涉及装饰装修及危险作业
    involves_three_in_one: Optional[bool] = None     # 涉及"三合一"场所
    involves_flammable_storage: Optional[bool] = None  # 涉及储存易燃易爆物品
    has_natural_gas: bool = Field(default=False)  # 天然气
    is_injection_molding: bool = Field(default=False)  # 注塑企业
    has_dormitory: bool = Field(default=False)  # 宿舍
    has_hazardous_chemicals: bool = Field(default=False)  # 危化品
    has_hazardous_waste: bool = Field(default=False)  # 危废品
    involves_dust_explosion: bool = Field(default=False)  # 粉尘爆炸
    is_new_energy_welding: bool = Field(default=False)  # 新能源电焊
    has_large_storage_tank: bool = Field(default=False)  # 大型储罐
    # ── 园区归属复核标记（台账核对：true=非新能源汽车产业园区，待人工复核删除）──
    out_of_park: bool = Field(default=False)
    out_of_park_note: Optional[str] = None  # 判定依据，如「光电园」「韩资园/步凤镇」
    # ── 长尾自定义字段：jsonb 键值对，如 {"联系人":"张三"}，读写均为 dict ──
    extra: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column("extra", JSONB, nullable=True),
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Material(SQLModel, table=True):
    """材料表 —— 一行一份文件，切片文本与向量全在 Qdrant，SQL 不设 chunk 镜像表。

    范围三形态（两字段表达）:
        A 公共通用: enterprise_codes='{}', scope_* 为空
        B 类型定向: scope_industry 或 scope_region 非空
        C 涉及企业: enterprise_codes 非空（可多家）
    约束: A 与 B∪C 二选一，B 与 C 可并存，检索取并集。
    范围上传时人工指定，不做内容自动识别。
    status 生命周期: pending → indexing → indexed / failed。
    """

    __tablename__ = "material"
    id: Optional[int] = Field(default=None, primary_key=True)
    uid: str  # UUID，生成后永不变（写进 Qdrant，材料稳定身份）
    filename: str
    storage_path: str  # → 本地文件
    file_hash: Optional[str] = None  # sha256，内容去重
    mime_type: Optional[str] = None
    source_format: Optional[str] = None  # pdf/docx/...
    page_count: Optional[int] = None
    uploaded_by: Optional[int] = Field(default=None, foreign_key="staff.id")
    qdrant_collection: Optional[str] = None
    status: str = Field(default="pending")  # pending/indexing/indexed/failed
    indexed_at: Optional[datetime] = None
    # ── 范围（决策五三形态）──
    enterprise_codes: Optional[str] = None  # text[]，弱引用 enterprise.code，default '{}'
    scope_industry: Optional[str] = None  # 面向某行业
    scope_region: Optional[str] = None  # 面向某区域
    scope_sync_pending: bool = Field(default=False)  # 范围同步脏标记
    created_at: datetime = Field(default_factory=datetime.now)


class InspectionRecord(SQLModel, table=True):
    """巡查隐患记录表 —— 一巡查多隐患，每行一条隐患+整改。

    模型: 一次到点巡查生成一个巡查批次（inspection_batch，如 INS-20260911-001
    或 Excel 导入的 2026-09），可覆盖多企业、每企业多条隐患。
    每行 = 一条隐患（hazard_index 区分同批次同企业的第 N 条）+ 其整改，
    巡查信息（日期/检查人/园区/行业）在同批次同企业内共享。
    隐患数 = 行数，count(*) 直接得总数。

    企业关联（2026-09 改造）：
      - enterprise_code + enterprise_name 是与 enterprise 表的业务关联键
        （Excel 导入通道直接写入，不加物理外键，应用层 resolve）；
      - enterprise_id 保留为数据指针（能按名称匹配到企业主表时回填，可为空）。
    """

    __tablename__ = "inspection_record"
    __table_args__ = (
        UniqueConstraint(
            "inspection_batch", "enterprise_id", "hazard_index",
            name="uq_inspection_hazard_identity",
        ),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    # ── 巡查归属（同批次共享）──
    inspection_batch: str = Field(index=True)  # 巡查批次号，如 INS-20260911-001 / 2026-09
    # 交办单文书编号（如 AHJ032026208），可空，同批次记录共享，编辑时批量同步
    notice_number: Optional[str] = Field(default=None, index=True)
    # ── 企业关联：纯 enterprise_id FK（100% 关联后冗余列 enterprise_code / enterprise_name 已删除）──
    enterprise_id: Optional[int] = Field(default=None, foreign_key="enterprise.id")
    # ── 隐患序号/总数（同批次同企业内 1..N）──
    hazard_index: Optional[int] = None        # 第几条隐患（拆分"1、…；2、…"得到）
    hazard_count_total: Optional[int] = None  # 该次检查发现隐患总数
    title: Optional[str] = None
    inspection_date: Optional[date] = None
    location: Optional[str] = None
    region: Optional[str] = None    # 所属园区镇街
    industry: Optional[str] = None  # 行业
    inspectors: Optional[str] = None  # 检查人（多人顿号分隔，Excel 原文）
    inspector_user_id: Optional[int] = Field(default=None, foreign_key="staff.id")
    # ── 隐患（每行一条）──
    hazard_level: Optional[str] = None  # low/medium/high（Excel 导入留空，agent 后续补）
    hazard_category: Optional[str] = None
    hazard_description: str = ""
    # ── 整改（每行独立）──
    rect_responsible_name: Optional[str] = None  # 整改责任人姓名（Excel 原文）
    rect_responsible_user_id: Optional[int] = Field(default=None, foreign_key="staff.id")
    rect_deadline: Optional[date] = None         # 整改期限（默认检查时间 +30 天）
    rect_completed_at: Optional[datetime] = None  # 实际整改完成时间
    review_date: Optional[date] = None           # 复查日期（完成整改时键入，精确到日）
    # ── 状态（每条隐患独立流转）──
    status: str = Field(default="open", index=True)  # open → rectifying → closed
    remark: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class AuditLog(SQLModel, table=True):
    """审计日志表 —— 操作留痕，只追加不修改不删除。

    决策三: 只记写操作 + 登录事件，读请求一律不记。
    白名单: material.upload/index/reindex/scope_update/delete ·
           inspection.create/assign/close · auth.login(失败必记) ·
           admin.staff.create/reset_password/deactivate · 各类删除必记。
    """

    __tablename__ = "audit_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    staff_id: Optional[int] = Field(default=None, foreign_key="staff.id")
    action: str  # rag.ask / material.upload / auth.login / admin.staff.create ...
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    request_payload: Optional[str] = None  # JSON string
    ip_address: Optional[str] = None
    latency_ms: Optional[int] = None
    status: str = Field(default="success")  # success/failed
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class FactoryInFactory(SQLModel, table=True):
    """厂中厂消防安全专项整治记录表 —— 一行一对出租方-承租方关系。

    厂中厂是出租方（房屋产权单位）与承租方之间的租赁关系记录。
    每行 = 一对（出租方, 承租方）+ 该次专项整治的隐患与整改。
    多条隐患以顿号分隔的文本存于 hazard_description；如需逐条统计可后续拆分。
    出租方/承租方都关联 enterprise 表（enterprise_id FK），原文姓名保留。
    inspection_batch 聚合同批次（如 "2026-厂中厂消防"），与企业表/巡查表共用筛选协议。
    """

    __tablename__ = "factory_in_factory"
    id: Optional[int] = Field(default=None, primary_key=True)
    # ── 巡查归属（同批次共享）──
    inspection_batch: str = Field(index=True)  # 如 "2026-厂中厂消防"
    region: Optional[str] = None    # 县（市、区）
    address: Optional[str] = None   # 具体地址
    # ── 出租方：纯 FK（lessor_name / lessor_contact_person / lessor_contact_phone 冗余列已删除，展示时 JOIN Enterprise）──
    lessor_enterprise_id: Optional[int] = Field(default=None, foreign_key="enterprise.id")
    lessor_use_area: Optional[str] = None    # 使用面积 ㎡ 或 "纯出租"
    lessor_hazard_description: Optional[str] = None  # 出租方隐患（多条顿号拼接）
    lessor_rect_status: Optional[str] = None        # 出租方整改情况
    # ── 承租方：纯 FK（tenant_name / tenant_contact_person / tenant_contact_phone 冗余列已删除）──
    tenant_enterprise_id: Optional[int] = Field(default=None, foreign_key="enterprise.id")
    tenant_area: Optional[float] = None     # 承租建筑面积 ㎡
    agreement_signed: Optional[bool] = None  # 是否签订消防安全管理协议
    tenant_hazard_description: Optional[str] = None  # 承租方隐患（多条顿号拼接）
    tenant_rect_status: Optional[str] = None        # 承租方整改情况
    # ── 专项整治信息（解析自备注）──
    site_category: Optional[str] = None    # 场所类别：丙类/丁类/厂中厂
    inspection_date: Optional[date] = None  # 排查时间
    inspectors: Optional[str] = None       # 排查人员
    rect_supervisor: Optional[str] = None  # 督改人员
    # ── 备注 ──
    remark: Optional[str] = None           # 原始备注全文
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class HazardousChemical(SQLModel, table=True):
    """危化品使用企业专项整治记录表 —— 一行一种危化品，企业可有多行。

    数据模型：
        一行 = 一家企业的一种危化品明细（名称/状态/储量/用途/存储等）。
        企业关联 enterprise 表（enterprise_id FK），原文姓名保留。
        与厂中厂表共用筛选协议，前端按企业名纵向合并单元格展示。
    """

    __tablename__ = "hazardous_chemical"
    id: Optional[int] = Field(default=None, primary_key=True)
    # ── 企业关联：纯 enterprise_id FK（enterprise_name / region / industry / scale 冗余列已删除，展示时 JOIN Enterprise）──
    enterprise_id: Optional[int] = Field(default=None, foreign_key="enterprise.id")
    # ── 危化品明细 ──
    hazchem_name: Optional[str] = Field(default=None, index=True)  # 危化品名称
    hazchem_state: Optional[str] = None       # 危化品状态（液态/固态/气态）
    reserve: Optional[str] = None             # 储量（原文，如 "20t"）
    max_reserve: Optional[str] = None         # 设计最大储量（原文）
    annual_usage: Optional[str] = None       # 年度使用量（原文）
    usage: Optional[str] = None              # 危化品用途
    storage_method: Optional[str] = None     # 存储方式
    storage_location: Optional[str] = None   # 存储位置
    major_hazard: Optional[str] = None       # 是否构成重大危险源
    remark: Optional[str] = None             # 备注
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ChatSession(SQLModel, table=True):
    """智能问答会话表 —— 归属 staff，一人多会话。

    Hermes API 是无状态的（每次请求带全量 messages），多轮上下文由
    rag-api 从本表 + chat_message 组装后转发，Hermes 不保存平台侧会话。
    UUID 主键暴露在前端 URL 中，不可枚举。
    """

    __tablename__ = "chat_session"
    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column("id", PGUUID(as_uuid=True), primary_key=True),
    )
    user_id: int = Field(foreign_key="staff.id", index=True)
    title: str = Field(default="新对话", max_length=200)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ChatMessage(SQLModel, table=True):
    """智能问答消息表 —— 一行一条 user/assistant 消息。

    流式中的 hermes.tool.progress 事件只做实时展示，不写入本表。
    删会话时消息随 ON DELETE CASCADE 一并清除。
    """

    __tablename__ = "chat_message"
    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column("id", PGUUID(as_uuid=True), primary_key=True),
    )
    session_id: uuid.UUID = Field(
        sa_column=Column(
            "session_id",
            PGUUID(as_uuid=True),
            ForeignKey("chat_session.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    role: str = Field(max_length=16)  # user | assistant
    content: str
    # Hermes 上游 usage 帧（仅 assistant 消息有；user/中断无 usage 时为 None）
    prompt_tokens: Optional[int] = Field(default=None)
    completion_tokens: Optional[int] = Field(default=None)
    total_tokens: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
