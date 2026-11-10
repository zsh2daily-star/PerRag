# Web 前端代码设计

> 适用状态：2026-10-04。基于 pure-admin-thin 6.2.0 脚手架二次开发的"企业监管平台"。
> 技术栈：Vue 3.5 + TypeScript + Vite 7 + Element Plus 2.11 + Pinia 3 + Vue Router 4（hash 模式）+ TailwindCSS 4，包管理 pnpm，开发端口 8848。

---

## 1. 目录结构

```
web/
├── src/
│   ├── main.ts                # 引导：加载 platform-config → pinia → router → 指令/全局组件 → 挂载
│   ├── App.vue                # el-config-provider 中文 + 全局 ReDialog 容器
│   ├── views/                 # 业务页面（见第 4 节）
│   ├── router/                # 路由实例、全局守卫、modules/ 静态路由模块
│   ├── layout/                # 整体布局（侧边栏/顶栏/多标签页/内容区/设置抽屉）
│   ├── components/            # Re* 全局共享组件（见第 5 节）
│   ├── composables/           # 组合式函数（见第 6 节）
│   ├── api/                   # 后端接口封装，与后端 router 一一对应（见第 7 节）
│   ├── store/modules/         # Pinia 六个 store（见第 8 节）
│   ├── utils/http/            # axios 单例封装
│   ├── utils/auth.ts          # token / user-info / multiple-tabs 存取
│   ├── types/                 # 目前仅 chat.ts；业务类型多在 api/*.ts
│   ├── directives/            # auth / perms / copy / longpress / ripple
│   ├── config/                # 启动 fetch /platform-config.json 注入全局 $config
│   ├── plugins/               # element-plus 按需注册、echarts 封装（echarts 当前未启用）
│   ├── style/ assets/         # 全局 SCSS、静态资源
│   └── mock/                  # vite-plugin-fake-server（仅 /get-async-routes 仍生效，返回空）
├── public/platform-config.json# 运行时配置（KeepAlive、MultiTagsCache、标题等）
├── vite.config.ts             # dev server 8848 + 后端 proxy
├── .env.development/.production
└── Dockerfile                 # pnpm build → nginx 静态托管（当前未纳入 compose 运行）
```

## 2. 路由与登录守卫

路由模块（[router/modules/](../web/src/router/modules/)）全部为**静态路由**（动态路由框架保留，mock 返回空数组），hash 模式：

| 路径 | name | 页面 | keepAlive / 权限 |
|---|---|---|---|
| `/dashboard` | Dashboard | 工作台 | 无 |
| `/chat/:sessionId?` | ChatPage | 智能问答 | keepAlive |
| `/enterprises` | EnterpriseList | 企业列表 | keepAlive |
| `/enterprises/confined-spaces` | ConfinedSpaceList | 有限空间管理（企业页薄包装） | keepAlive |
| `/enterprises/hazardous-chemicals` | HazardousChemicalList | 危化品管理（企业页薄包装） | keepAlive |
| `/enterprises/factory-in-factory` | FactoryInFactoryList | 厂中厂整治 | keepAlive |
| `/materials` | MaterialList | 材料管理 | keepAlive |
| `/inspection` | InspectionList | 巡查整改单 | keepAlive |
| `/system/staff`、`/system/audit` | — | 账号管理 / 审计日志 | roles: ["admin"]，无 keepAlive |
| `/login`、`/error/403|500`、`/redirect/*` | — | 登录与错误页 | — |

全局守卫（[router/index.ts](../web/src/router/index.ts) beforeEach）：

1. **登录态三项齐全才放行**：Cookie `multiple-tabs= true` + localStorage `user-info` 存在 + token Cookie `authorized-token`；不满足则除 `/login` 外一律清 token 跳登录。
2. `to.meta.roles` 与用户 roles 无交集 → `/error/403`。
3. 刷新时 wholeMenus 为空会重建路由并补当前标签；页面标题取 `meta.title ｜ 企业监管平台`。
4. keepAlive：路由 meta 标记后把**路由 name** 加入 permission store 的 `cachePageList`，由内容区 `<keep-alive :include>` 按**组件 name** 匹配。

## 3. 布局与页面渲染机制

[layout/](../web/src/layout/)：左侧 NavVertical 菜单（来自 permission store 的 wholeMenus），右侧顶栏（面包屑、菜单搜索、全屏、通知、改密、退出）+ 多标签页（右键关闭/刷新）+ 内容区。

两个必须注意的机制（改页面前先看）：

- **内容区以 `:key="route.fullPath"` 渲染页面组件**（[lay-content/index.vue](../web/src/layout/components/lay-content/index.vue)）：params/query 任意变化都会**销毁重建整个页面组件**。聊天页因此不能在发送消息瞬间改 URL（见第 9 节）。
- 多标签关闭联动 `cacheOperate("delete")` 清 keep-alive；全局开关在 platform-config.json 的 `KeepAlive`（true）。

## 4. 业务页面

| 页面 | 要点 |
|---|---|
| [chat](../web/src/views/chat/index.vue) | 智能问答：会话侧栏 + 消息流 + SSE，详见第 9 节 |
| [dashboard](../web/src/views/dashboard/index.vue) | 工作台：4 张统计卡（未闭环/超期/本月新增/材料总数）、等级分布、超期 Top5、最近材料；纯 Element 组件，未用 echarts |
| [enterprises](../web/src/views/enterprises/index.vue)（约 2400 行，台账基座页） | 关键词搜索、表头漏斗筛选（useHeaderFilters，POST /enterprises/query）、列显隐（localStorage）、表头拖拽换序（页内原生 HTML5 drag）、新建/编辑弹窗（含 extra 自定义行）、多选批量删除/批量改、客户端 XLSX 导出（最多 5000 条）。通过 props（presetFilter / columnKeys / storageKey）复用于下面两个专页 |
| confined-spaces / hazardous-chemicals | 48/51 行薄包装：固定筛选条件 + 固定列集，渲染企业页组件 |
| [factoryInFactory](../web/src/views/factoryInFactory/index.vue)（约 1230 行） | 与企业页同构（useColumnDrag 已抽 composable）；额外有 **Excel 导入**（el-upload）、未关联企业标红、按出租方行合并、批次下拉 |
| [inspections](../web/src/views/inspections/index.vue)（约 1310 行） | 复合筛选、**Excel 导入**、未匹配企业重关联（含冲突批次提示）、派发整改/完成闭环、详情时间线、同企业同批次单元格合并、超期行高亮、批量改 |
| [materials](../web/src/views/materials/index.vue)（约 720 行） | 材料登记（上传只登记元数据 + 适用范围 A/B/C，无二进制上传）、触发索引/重建、范围 PATCH、佐证分片查看、状态机；存在 indexing 行时 3s 轮询自停 |
| login | 账号密码 → POST /auth/login → setToken → initRouter → 首页 |
| system/staff | 账号 CRUD、重置密码、停用/启用（admin） |
| system/audit | 审计日志查询；前端维护 action 中文映射表 |

## 5. 共享组件（components/）

| 组件 | 用途 |
|---|---|
| ReFilterHeader | 嵌在 el-table-column 表头的筛选 popover（text/number/date/enum/bool + 为空/非空），企业/厂中厂/巡查页使用 |
| ReColumnSettings | 列显隐勾选面板（分组/全选），状态由 useColumnPrefs 提供 |
| ReContactCell | 联系人单元格：姓名上行、电话灰色下行 |
| ReBulkEdit | 批量修改弹窗：选字段后按类型出新值控件 |
| ReFilterBuilder | 嵌套 AND/OR 条件构建器（含 types.ts 的 FilterNode 类型与工厂函数）。**组件 UI 当前已建未用**，页面只用其类型定义 |
| ReDialog | 命令式弹窗（App.vue 挂唯一容器） |
| ReAuth / RePerms | 按 meta.auths / 用户 permissions（支持 `*:*:*`）控制元素显隐 |
| ReText / ReSegmented / ReCol / ReIcon | 截断悬浮、分段器、栅格封装、图标体系 |
| ChangePasswordDialog | 改密弹窗（8-18 位两种组合校验） |

## 6. composables/

| 函数 | 职责 |
|---|---|
| useChatStream | SSE 流式问答（fetch 手动分帧），见第 9 节 |
| useHeaderFilters | 表头筛选状态机：各列条件同步进 FilterGroup 树并触发查询；数组列用 overlaps、`__null__` 哨兵拼 is_null/is_not_null |
| useColumnPrefs | 列显隐 + 顺序的 localStorage 偏好（双 key、迁移钩子、全选/反选） |
| useColumnDrag | 原生 HTML5 表头拖拽换序（厂中厂页使用；企业页另有一份页内实现） |

## 7. api 层与请求封装

`src/api/` 每文件对应一个后端模块：user（/auth）、enterprises、inspections、factoryInFactory、materials、dashboard、staff、audit、chat。统一返回后端 `{success, data}` 响应体，业务页面 `.then(res => res.data)`。

[utils/http](../web/src/utils/http/index.ts)（axios 单例）：

- **无 baseURL**，全部相对路径：开发态由 [vite.config.ts](../web/vite.config.ts) proxy 把 `/auth /admin /enterprises /factory-in-factory /inspection-records /materials /evidence /dashboard /chat` 转发到 `http://localhost:8000`（/chat 为 SSE 透传不缓冲）；生产同源。
- 请求拦截：注入 `Authorization: Bearer <accessToken>`（token 取自 Cookie，回退 localStorage）；本地发现过期则用 refreshToken 刷新，期间请求排队。
- 响应拦截：直接返回 `response.data`，**无全局错误弹窗**；401 清登录态并跳 `/login`。
- timeout 10s（SSE 不走此封装，单独 fetch）。

## 8. Pinia store

| store | 内容 |
|---|---|
| user | token 之外的用户档案：username/nickname/roles/permissions（初始从 localStorage 恢复）；login / logout / refresh |
| permission | 静态菜单 wholeMenus、拍平路由、**cachePageList（keep-alive 名单）** |
| multiTags | 多标签页数组（配置项 MultiTagsCache=false，仅内存） |
| app | 侧边栏开合、布局模式（vertical/horizontal/mix）、设备断点 |
| settings / epTheme | 标题/固定头；Element 主题色与明暗模式 |

## 9. 智能问答页与 SSE（重点）

页面结构：左 240px 会话栏（新建/切换/删除，标题取首句），右消息区（空态欢迎语 + 3 个快捷提问；markdown-it 渲染助手消息，链接强制新窗口；工具进度行 running 转圈/completed ✓）+ 底部输入（Enter 发送、Shift+Enter 换行、流式中显示"停止"）。

[useChatStream.ts](../web/src/composables/useChatStream.ts) 不用 EventSource（OpenAI 协议是 POST 且要带 JWT），用原生 fetch 手动解析 SSE：

1. `POST /chat/sessions/:id/messages`，带平台 JWT 与 AbortController signal。
2. `resp.body.getReader()` + TextDecoder 累积缓冲，按 `\n\n` 切帧；帧内解析 `event:` / `data:`；`data: [DONE]` 结束。
3. 普通帧取 `choices[0].delta.content` 回调 onDelta；`event: hermes.tool.progress` 帧回调 onTool（tool/emoji/label/status，completed 帧无 emoji/label，前端兜底）；`obj.error` 回调 onError。
4. "停止"即 abort()，主动中断不报错、保留已生成文本；组件卸载自动 abort。

会话与路由的时序约束（由内容区 `:key=fullPath"` 机制决定）：

- 首条发送时只创建会话、持有 currentId，**不改 URL**；
- 流结束（onDone/onError）后延迟 500ms 才 `router.replace('/chat/:id')`——既避免组件被立即重建而掐断 SSE，也等后端 finally 把助手消息落库后再拉历史。
- 增量更新必须修改 reactive 数组内的代理对象（push 后取 `messages.value[len-1]`），持有原始对象引用改内容不会触发渲染。

## 10. 构建与环境

- `pnpm dev`（4GB 内存上限，端口 8848）、`pnpm build`（8GB）、`pnpm typecheck`（tsc + vue-tsc）。
- Node 要求 ^20.19 或 >=22.13，强制 pnpm。
- 关键依赖：markdown-it 14（聊天渲染）、xlsx 0.18（客户端导出）、sortablejs（仅脚手架表格条/菜单搜索用）、axios、dayjs、vue-tippy；echarts 已安装但未启用。
- 生产镜像为 nginx 纯静态（[web/Dockerfile](../web/Dockerfile)），需自行保证同源反代到 rag-api:8000。
