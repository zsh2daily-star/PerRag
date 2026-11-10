# 前端工程说明（企业监管平台）

> 生成时间 2026-09-11 · 基于 pure-admin-thin 模板（Vue3 + Vite + TS + Element Plus）
> 设计文档见 `docs/FRONTEND_PRD.md`，数据契约见 `docs/POSTGRESQL_SCHEMA.md` / `docs/QDRANT_SCHEMA.md`

---

## 一、技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 框架 | Vue 3.5 + `<script setup lang="ts">` | 组合式 API |
| 构建 | Vite 6 | 开发 `pnpm dev`，生产 `pnpm build` |
| UI | Element Plus | 全局注册，按需引入 |
| 状态 | Pinia | 用户/权限/标签页/主题 |
| 路由 | Vue Router 4 | Hash 模式（开发） |
| 请求 | Axios（模板封装 `src/utils/http/`） | 拦截器注入 Bearer、401 处理 |
| Mock | vite-plugin-fake-server | 开发环境内置 mock，无需后端 |
| 样式 | SCSS + Tailwind CSS + Element Plus 变量 | 主题可定制 |

---

## 二、目录结构（只列我们关心的，模板自带的不展开）

```
web/
├── src/
│   ├── api/                    # 9 个 API 模块：封装 HTTP 调用
│   │   ├── user.ts             #   登录 / 刷新 token（已改：加 is_admin）
│   │   ├── enterprises.ts      #   企业 CRUD
│   │   ├── materials.ts        #   材料 CRUD + 索引 / 重建 / 范围 / 证据
│   │   ├── inspections.ts      #   巡查整改 CRUD + 状态流转 + 统计
│   │   ├── staff.ts            #   账号管理 CRUD + 重置密码 / 停用
│   │   ├── audit.ts            #   审计日志查询
│   │   ├── chat.ts             #   智能问答 /query
│   │   ├── dashboard.ts        #   工作台统计
│   │   └── routes.ts           #   （模板遗留，未使用）
│   │
│   ├── router/modules/         # 6 个路由模块：定义菜单和页面路径
│   │   ├── home.ts             #   工作台 /dashboard
│   │   ├── dashboard.ts        #   智能问答 /chat
│   │   ├── enterprises.ts      #   企业管理 /enterprises
│   │   ├── materials.ts        #   材料管理 /materials
│   │   ├── inspections.ts      #   巡查整改 /inspections
│   │   ├── system.ts           #   系统管理 /system/staff + /system/audit（roles: admin）
│   │   └── remaining.ts        #   登录 / 404 / 500 等非菜单路由（模板自带）
│   │
│   ├── views/                  # 7 个业务页面 + 模板页
│   │   ├── dashboard/          #   工作台
│   │   ├── chat/               #   智能问答
│   │   ├── enterprises/        #   企业管理
│   │   ├── materials/          #   材料管理
│   │   ├── inspections/        #   巡查整改
│   │   ├── system/staff/       #   账号管理（仅 admin）
│   │   ├── system/audit/       #   审计日志（仅 admin）
│   │   ├── login/              #   登录页
│   │   └── error/              #   403 / 404 / 500
│   │
│   ├── store/modules/
│   │   ├── user.ts             # 已改：登录状态 + is_admin → roles 映射
│   │   └── permission.ts       # 模板自带：动态路由 / 菜单生成
│   │
│   ├── utils/
│   │   ├── http/index.ts       # Axios 封装（拦截器、token 注入、401）
│   │   └── auth.ts             # 已改：DataInfo 接口加 is_admin 字段
│   │
│   ├── layout/                 # 模板自带：顶栏 / 侧边栏 / 标签页 / 搜索
│   ├── components/Re*/         # 模板自带：对话框 / 图标 / 表格工具栏 / 权限按钮
│   ├── directives/             # 模板自带：权限 / 复制 / 长按 / 水波纹
│   └── style/                  # 全局样式 + 暗色模式 + EP 主题覆盖
│
├── mock/                        # 9 个 mock 文件（开发环境假数据）
│   ├── login.ts                #   admin/admin123 + common/common123
│   ├── enterprises.ts          #   10 家企业
│   ├── materials.ts            #   8 份材料（pending / indexing / indexed / failed）
│   ├── inspections.ts          #   10 条巡查单（含超期）
│   ├── staff.ts                #   5 个账号
│   ├── audit.ts                #   15 条审计日志
│   ├── chat.ts                 #   关键词路由 sql / rag / hybrid
│   ├── dashboard.ts            #   工作台统计
│   ├── asyncRoutes.ts          #   动态路由（当前返回空，用静态路由）
│   └── refreshToken.ts         #   刷新 token（模板自带）
│
├── build/                       # Vite 构建配置（CDN / 压缩 / 依赖预优化）
├── public/                     # 静态资源 + platform-config.json（平台标题）
├── .env                        # 基础环境变量（端口 8848 / 平台名称）
├── .env.development            # 开发环境（Hash 路由）
├── .env.production             # 生产环境
├── Dockerfile                  # 两阶段构建：Node 编译 → Nginx 托管
├── package.json                # 依赖 + 脚本
└── vite.config.ts              # Vite 配置（proxy 目前为空）
```

---

## 三、当前状态：M1 完成（全量界面 mock 走通）

| 模块 | 路由 | mock 数据 | 交互完整度 |
|---|---|---|---|
| 工作台 | `/dashboard` | 统计 + 超期 + 最近材料 | ✅ 读 |
| 智能问答 | `/chat` | 关键词路由三意图 | ✅ 发消息 + 徽标 + 引用展开 |
| 企业管理 | `/enterprises` | 10 家 | ✅ CRUD + 搜索 + 弹窗 |
| 材料管理 | `/materials` | 8 份（四态） | ✅ 列表 + 上传 + 索引/重建/证据/范围 + 状态轮询 |
| 巡查整改 | `/inspections` | 10 条 | ✅ 列表 + 新建 + 状态流转 + 超期高亮 + 时间线 |
| 账号管理 | `/system/staff` | 5 人 | ✅ CRUD + 初始密码 + 重置 / 停用 |
| 审计日志 | `/system/audit` | 15 条 | ✅ 只读 + 筛选 + 展开行 |

**登录账号**：`admin / admin123`（管理员，全菜单）· `common / common123`（监管员，无系统管理菜单）

---

## 四、权限适配

模板自带完整 RBAC（角色 / 权限管理页），我们只有 `is_admin` 布尔位，做了以下适配：

```
登录响应
  ├── is_admin=true  → roles=["admin"]  → 看到全部菜单（含 /system/*）
  └── is_admin=false → roles=["staff"]  → 看不到 /system/* 菜单
```

- 路由用 `meta.roles: ["admin"]` 过滤（见 `src/router/modules/system.ts`）
- 非管理员手输 `/system/staff` 会被路由守卫拦截 → 403 页
- 后端兜底：账号管理接口后端额外校验 `is_admin=true`
- Token 刷新：后端暂无 refresh 端点，当前配 401 → 清会话回登录页

---

## 五、Mock 机制

Mock 通过 `vite-plugin-fake-server` 在开发环境拦截 HTTP 请求，**不走网络**。所有 mock 文件在 `mock/` 目录，使用 `defineFakeRoute` 模式定义路由。

**对接真实后端时**：Vite 配置中关闭 mock 插件即可（或按模块渐进关闭）。`src/api/*.ts` 里的函数已经是正确的 HTTP 调用，只是当前被 mock 拦截了。

---

## 六、和后端的关系

**当前完全独立**——前端不依赖后端代码，只依赖 HTTP 接口契约：

```
web/src/api/enterprises.ts     ←→  app/main.py 的 GET /enterprises 端点
web/src/api/materials.ts       ←→  app/main.py 的 /materials 系列端点
web/src/api/chat.ts            ←→  app/main.py 的 POST /query 端点
...
```

对接时需要做的：
1. 后端端点上线
2. 在 `vite.config.ts` 的 `server.proxy` 配置后端地址
3. 关闭对应 mock 文件（或整体关闭 mock 插件）
4. API 函数无需改动（URL 和参数已对齐 PRD 契约）

---

## 七、后续待完成清单

### P0 · 后端依赖（阻塞接真）

| # | 事项 | 涉及文件 | 依赖 |
|---|---|---|---|
| 1 | 登录响应补 `is_admin` / `name` 字段 | `mock/login.ts` → 关闭 | 后端 `POST /auth/login` |
| 2 | 新增 `GET /auth/me` 获取当前用户 | `src/api/user.ts` | 后端新增端点 |
| 3 | 企业 CRUD 端点 | `mock/enterprises.ts` → 关闭 | 后端 `/enterprises` |
| 4 | 材料 multipart 上传 bug 修复 + 契约改为 `uid/scope_*` | `src/api/materials.ts` | 后端 `POST /materials` |
| 5 | 巡查整改 CRUD + 状态流转 + 统计 | `mock/inspections.ts` → 关闭 | 后端 `/inspection-records` |
| 6 | 账号管理端点（is_admin 守卫） | `mock/staff.ts` → 关闭 | 后端 `/admin/staff` |
| 7 | 审计日志查询端点 | `mock/audit.ts` → 关闭 | 后端 `/admin/audit-logs` |

### P1 · 前端待改

| # | 事项 | 说明 |
|---|---|---|
| 1 | `vite.config.ts` 配置 proxy | 对接后端时设置 `server.proxy` 转发 `/api` 到 FastAPI |
| 2 | Token 刷新逻辑 | 后端补 refresh 端点后，开启模板自带的静默刷新 |
| 3 | `POST /materials/{uid}/index` 路径修 | 当前接口表仍用 `{id}`，需统一为 `{uid}` |
| 4 | SSE 预留 | `/chat` 当前非流式，后端 SSE ready 后改用 EventSource |
| 5 | 材料上传真实 multipart | 当前 mock 不处理真实文件上传，接真后需封装 FormData |
| 6 | 设计 token 冻结 | 当前用模板默认主题色，需按品牌定制主色 / 字号 / 间距 |
| 7 | 清理空目录 | `src/views/permission/` 和 `src/views/welcome/` 是删除文件后的空壳，可删 |

### P2 · 功能增强

| # | 事项 | 说明 |
|---|---|---|
| 1 | 工作台图表 | 当前用 el-progress 条形图，可升级为 ECharts 饼图 / 柱状图 |
| 2 | 材料范围更新端点 | `PATCH /materials/{uid}/scope` 后端尚未实现 |
| 3 | 重建索引端点 | `POST /materials/{uid}/reindex` 后端尚未实现 |
| 4 | 证据溯源路径 | `GET /evidence/{material_uid}` 标识需从数字 id 改为 uid |
| 5 | 对话内 SSE 流式 | `/chat` 代理 + Hermes，PRD 标为"后期货" |
| 6 | Dockerfile lock | 部署前确保 `pnpm-lock.yaml` 与 `package.json` 一致 |

---

## 八、开发命令

```bash
# 安装依赖（需要 Node 22 + pnpm 10）
pnpm install

# 启动开发服务器（http://localhost:8848）
pnpm dev

# 类型检查
pnpm typecheck

# 生产构建（输出到 dist/）
pnpm build

# 预览生产构建
pnpm preview
```

> 环境变量在 `.env` / `.env.development` / `.env.production` 中配置。
> Mock 仅在开发环境生效，生产构建后 mock 不工作。
