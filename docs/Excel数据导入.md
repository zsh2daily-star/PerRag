# Excel 数据导入与企业补全

> 适用状态：2026-10-05。本文讲台账 Excel 入库的**业务知识与分层设计**：30 个 Sheet 怎么分类、
> 列怎么映射到字段、四层架构如何合并/聚合、以及"以 SQL 主体为准反向补空字段"的两条补全链路。
> 工具清单与参数见 [MCP 工具调用](mcp工具调用.md) 第 4/5 节；表字段定义见 [SQL 表结构](sql表结构.md)。
>
> 代码：
> - L1~L3 写入层：[import_writer.py](../app/services/svc_Import/import_writer.py)
> - L4 解析聚合层：[import_parser.py](../app/services/svc_Import/import_parser.py)
> - 信息池正则补全：[backfill_service.py](../app/services/svc_Import/backfill_service.py)
> - 字段 schema 与强校验：[enterprise_fields.py](../app/services/svc_Import/enterprise_fields.py)
> - MCP 工具：[excel_import.py](../app/mcp/tool_mix/excel_import.py)（8 个）、[backfill.py](../app/mcp/tool_mix/backfill.py)（5 个）

---

## 一、两条链路，不要混淆

| | **Excel 逐行导入**（正向） | **企业补全**（反向） |
|---|---|---|
| 方向 | Excel 行 → 按列映射 → 写入业务表，**可新建记录** | SQL 已有企业为准 → 只补空字段，**不新建、不覆盖** |
| 输入 | 结构化台账，列名精确映射 | A：Excel 散落信息自由聚合；B：知识库非结构化文档 |
| 工具组 | excel_import 组 8 个 | backfill 组 5 个（分 A/B 两条链路） |
| 典型场景 | 年度台账入库、属性表打标签 | 新能源园区 337 家企业空字段补全 |

---

## 二、数据源结构

**默认文件**：`各类企业情况汇总（2026）.xlsx`（约 1.1MB，30 个 Sheet），
容器内路径 `/app/data/uploads/各类企业情况汇总（2026）.xlsx`
（[import_parser.py](../app/services/svc_Import/import_parser.py) 的 `DEFAULT_XLSX_PATH`，工具入参 `file_path` 可覆盖）。

### 2.1 Sheet 五分类

| 分类 | 表头特征 | 代表 Sheet | 处理方式 |
|---|---|---|---|
| **标准主表** | 表头在 row0，每行一家 | `新能源工业企业名单`（24 列） | 最标准，字段映射完整 |
| **多行表头** | 真实表头在 row1~row3，需自动定位 | `有限空间企业基本情况（29家）`、`危化品使用企业`、`粉尘涉爆企业名单` | `locate_header` 扫前 8 行定位 |
| **一企多行** | 一家企业属性分散在多行 | `危化品使用企业`（每种危化品一行） | L4 按身份键聚合 |
| **打标签名单** | 企业名 + 共同属性 | `消防安全重点单位` | 按名匹配已有企业 → 单字段打标签 |
| **纯名单/统计/系统表** | 内容极简甚至为空 | `危化品统计表`、`WpsReserved_CellImgList` | 全簿规划建议 skip；`WpsReserved`/账号密码类按关键词自动跳过 |

另有一类**双实体关系表**（如租赁/厂中厂 Sheet，同时出现出租方与承租方）：
两个名称都是独立企业，不能并入属性，全簿规划标 `review` 要求人工确认。

### 2.2 主表 `新能源工业企业名单` 列 → enterprise 字段映射

| Excel 列 | enterprise 字段 | 说明 |
|---|---|---|
| 企业名称 | `name` | 必填，身份键 |
| 统一社会信用代码 | `code` | 18 位，身份键（优先判重） |
| 国民经济分类 | `extra.国民经济分类` | 原文保留（如"汽车制造业"），`industry` 只存大类；规则里以"国名经济分类"错别字做容错命中 |
| 行业（冶金、有色、建材、机械、轻工、纺织…） | `industry` | 不强制枚举，按 Excel 值落文本 |
| 规上/规下 | `enterprise_scale` | 枚举：规上/规下 |
| 企业总人数（含外包工） | `employee_count` | int |
| 是否使用危化品 | `has_hazardous_chemicals` | bool |
| 是否涉及有限空间（地上/地下） | `confined_space` + 7 个 `confined_space_*` 明细列 | 枚举列落地上/地下；数量/作业人数/危险因素/作业主体等落明细字段 |
| 是否涉及粉尘爆炸 | `involves_dust_explosion` | bool；粉尘种类明细进 `extra.粉尘种类` |
| 是否使用天然气 | `has_natural_gas` | bool |
| 是否注塑 | `is_injection_molding` | bool |
| 劳动密集型企业 | `enterprise_category` | **多值数组**：劳密/厂中厂出租/厂中厂承租/小场所/其他 |
| 涉及大型储罐（数量、介质等） | `extra.涉及大型储罐` + `has_large_storage_tank` | Excel 列是描述文本（"2只2.5m³"），原文进 extra |
| 法定代表人 | `legal_person` | 姓名+电话合并存同一字段（电话列经 `_merge_phone:` 追加） |
| 主要负责人姓名/电话 | `person_in_charge` | 同上 |
| 安全负责人/安全员 姓名/电话 | `safety_officer` | 安全员等同安全负责人，多列取非空合并 |
| 地址 | `address` | |
| 街道 | `extra.街道` | |
| 区/县 | **不自动映射** | region 保持园区口径（如"新能源园区"），行政区列不覆盖；需要时人工显式映射到 `extra:行政区` |

危化品类属性表另有 11 个明细字段：`hazchem_used_names`、`hazchem_names`、
`hazchem_op_subject`（作业主体）、`hazchem_major_hazard`（是否重大危险源）、
`hazchem_state`、`hazchem_op_cycle`、`hazchem_usage`、`hazchem_annual_usage`、
`hazchem_storage`、`hazchem_reserve`、`hazchem_fixed_workers`。

---

## 三、四层架构

```
L1  write_fields / create_record     最小写入单元：只管写，不判断
L2  FieldSpec + merge_field          判断层：字段规格 → 合并值或 SKIP
L3  TableWriter.preview / commit     编排层：preview=resolve+L2(不写) / commit=L1 写
L4  import_parser.aggregate_by_key   解析+聚合：Excel 多行 → 每企业一个 (identity, fields)
TOOL app/mcp/tool_mix/excel_import.py   Agent 入口：8 个工具分层暴露
```

每张 SQL 表一个 `TableWriter` 子类，目前注册三个：
**enterprise / material / inspection**（[excel_import.py](../app/mcp/tool_mix/excel_import.py) 的 `WRITERS`），
工具用 `table` 参数选择。巡查表支持无表头 Sheet（首行即数据，按列字母位置 `positional_col_map` 映射）
和一行多隐患拆分（`expand_skeletons` 钩子）。

### 层间数据流（Agent 编排，每层边界可见可控）

```
excel_sheet_parse (L4)
   输入：sheet_name + col_map（来自 inspect/plan 的建议，经人工核对）
   输出：[{identity, fields}, ...] 每企业一个元组 + parse_failures
        ↓
sql_record_preview (L3+L2)   —— 不写库
   输出 PreviewResult：
     action: created/updated/skipped/failed
     matched_field（靠哪个身份键命中）
     changes / important_changes（企业类型、风险 bool 变更单独标出）
     field_details: [{field, current, incoming, merged}] 每字段明细
     errors
        ↓ Agent 审阅：正常→commit；resolve 失败→跳过/先建/问用户；important 变更→请用户确认
sql_record_commit (L1)       —— 落库 + 审计
   输出 WriteResult；同批传相同 batch_id 聚合成一份批次报告
```

---

## 四、L2 字段规格与合并策略

### 4.1 四个维度（FieldSpec）

| 维度 | 取值 | 作用 |
|---|---|---|
| `is_identity` | True/False | 身份键更新时永不覆盖（SKIP） |
| `data_type` | `string` / `value` / `array` / `string_array` | 决定合并机制 |
| `allowed` | 枚举值域元组 | 合并时容错归一到合法枚举，越界 SKIP |
| `audit_weight` | `important` / `general` | important 变更在报告中突出 |

### 4.2 合并策略推导（is_identity × data_type）

| 身份键? | data_type | 策略 | 说明 |
|---|---|---|---|
| 是 | 任意 | **SKIP** | 更新时不碰身份键 |
| 否 | string | **追加**（默认）/ 覆盖（`override=true`） | 新值空→SKIP；枚举字段子串命中 `allowed` 后归一；新旧都非空且不同才覆盖 |
| 否 | value | **后来值** | 新值非空即用（bool/int 统一），空保留旧值；int 转换失败 SKIP |
| 否 | array | **key 级合并** | jsonb（extra），只增不删 |
| 否 | string_array | **list 级 union** | 去重保序、旧值在前、只增不删；incoming 经 `allowed` 子串过滤 |

**string 的子串防退化**：新旧值都非空且不同时，若新值是旧值子串
（如"王彬" vs "王彬 18068890557"）→ SKIP 保留信息更全的旧值；反之用新值。

### 4.3 enterprise 表规格现状

| 字段 | 类型 | 权重 | 取值域/说明 |
|---|---|---|---|
| `name` / `code` | 身份键 | — | 更新永不覆盖 |
| `enterprise_category` | string_array | **important** | 劳密/厂中厂出租/厂中厂承租/小场所/其他 |
| 8 个风险 bool | value | **important** | `has_natural_gas`、`is_injection_molding`、`has_dormitory`、`has_hazardous_chemicals`、`has_hazardous_waste`、`involves_dust_explosion`、`is_new_energy_welding`、`has_large_storage_tank` |
| `asset_attribute` | string | general | 韩资/台资/其他 |
| `enterprise_scale` | string | general | 规上/规下 |
| `fire_level` | string | general | 二级/三级/重点单位 |
| `confined_space` | string | general | 地上/地下（另有 7 个 `confined_space_*` 文本明细列） |
| 11 个 `hazchem_*` | string | general | 危化品明细文本 |
| `industry`/`region`/`address`/`legal_person`/`person_in_charge`/`safety_officer` | string | general | — |
| `employee_count`/`annual_output_value` | value(int) | general | — |
| `extra` | array(jsonb) | general | key 级合并，同 key 多值聚成数组 |

---

## 五、L4 解析与聚合

### 5.1 值规范化（normalize_row）

| 脏值模式 | 函数 | 规则 |
|---|---|---|
| 是/否类 | `parse_bool` | "是"/"Y"/"√"→True，"否"/"N"/"×"→False |
| 人数类 | `parse_int` | "120人"/"120"→120 |
| 枚举类 | `parse_enum` | "规上企业"→"规上"（容错包含匹配） |
| 全角空格/换行 | `clean_cell` | 全角→半角，去换行，strip |

规范化器从 `field_specs` 推导，无需按字段硬编码；规范化失败的行进 `parse_failures`（带行号+企业名提示），不阻塞其他行。

### 5.2 按身份键聚合（aggregate_by_key）

```
Step 1  全部非空身份键拼成复合分组键（code + name 都参与，不能只取第一个——
        复合主键表如隐患表 batch+enterprise_name+hazard_index 只取一个会误并组）
Step 2  同组多行经 _merge_skeletons 聚合
Step 3  拆成 (identity_dict, fields_dict)，再叠加 Sheet 名标签
```

同一企业多行的合并规则：

| 字段类型 | 规则 | 示例 |
|---|---|---|
| 标量(str/int) | 最后一个非空覆盖 | 地址以最后填的为准 |
| bool | 任一为真 | True + False → True |
| dict(extra) | key 级合并，同 key 多值聚数组 | 危化品:硫酸 + 危化品:盐酸 → {危化品:[硫酸,盐酸]} |

竖向合并单元格展开后身份键全空的明细行，自动**归属上一企业**。

### 5.3 Sheet 名标签自动注入（sheet_tag_rules）

顺序即优先级，首个命中生效：

| Sheet 名含关键词 | 注入字段 | 值 |
|---|---|---|
| 消防安全重点单位 | `fire_level` | 重点单位 |
| 有限空间 | `confined_space` | 从行内列取（地上/地下） |
| 危化品 / 危险品 | `has_hazardous_chemicals` | True |
| 粉尘 | `involves_dust_explosion` | True |
| 注塑 | `is_injection_molding` | True |
| 天然气 | `has_natural_gas` | True |
| 宿舍 | `has_dormitory` | True |
| 焊机 | `is_new_energy_welding` | True |
| 危废 / 危险废物 | `has_hazardous_waste` | True |
| 劳密 / 劳动密集 | `enterprise_category` | 劳密（数组追加） |

### 5.4 列映射规则（col_map_rules）的几个坑

规则按顺序首次命中，声明顺序本身就是业务知识：

1. **人员电话先于姓名**：`主要负责人电话`→`_merge_phone:person_in_charge` 等必须排在
   通用"负责人"之前，否则电话列被子串误映射成姓名。
2. **"是否"前缀精确匹配 bool**：`是否使用危化品`→bool；而`使用危化品名称`、`年度使用量`、
   `危化品储量`等明细列映射到 `hazchem_*` 或 extra——曾出现 47000 吨储量被误当 bool 的事故。
3. **专有长名在前**：`有限空间作业周期` 必须排在危化品的通用 `作业周期` 之前。
4. **行政区不落 region**：region 保持园区口径，`区/县`、`所在县区`不映射或进 extra。
5. col_map 目标值支持 `extra:自定义键名`，把无对应字段的列保留进 jsonb。

---

## 六、L3 preview / commit / flag

- **preview**（不写库）：五级 resolve 匹配库内记录——
  code 精确 → name 精确 → 归一化名精确（全角转半角、剥离末尾厂房号 `#9#10#`）
  → 归一化双向包含相似（双方 ≥6 字、长度差 ≤4 且唯一候选）→ 地址/行业交叉验证；
  再逐字段 merge，返回 `field_details`；无变化→skipped，异常→failed。
- **commit**（写库）：不存在→`create_enterprise`（复用 code 格式/枚举校验）；
  存在→有变化才 `write_fields`；幂等：相同 fields 二次提交→skipped。
- **flag**（便捷打标）：只改一个 bool/枚举字段，字段必须在 field_specs 声明且非身份键；
  企业不存在直接 failed（"请先导入主表"），不新建；`dry_run` 默认 true。
- **批次报告**：commit/flag 传相同 `batch_id` 聚合成一份报告
  （counts/failures/important_changes），进程内存保留最近 20 批，容器重启清空。

---

## 七、全簿规划：excel_workbook_plan

多 Sheet 工作簿推荐入口，一次扫描给出每个 Sheet 的导入建议，
业务启发式词表由 writer 的 `workbook_hints` 声明（强实体列、关系表信号、跳过词等）：

| recommendation | 含义 | 处理 |
|---|---|---|
| `mapped` | 列映射完整、与库内实体匹配率高 | 可直接 parse→preview→commit |
| `tag_only` | 只有企业名+共同属性 | 走 flag 打标签 |
| `review` | 低置信改名候选 / 双实体关系表 | **必须人工确认**后再导 |
| `skip` | 统计表、系统保留表、账号密码表 | 忽略 |

---

## 八、企业补全：以 SQL 主体为准反向填空

补全组所有工具遵循三条铁律：**只补库内已存在企业的空字段、不新建、不覆盖已有值**；
写库工具默认 `dry_run=true`；服务端永远做强校验，不信任调用方提交的值。

### 8.1 链路 A：Excel 信息池 + 正则（批量）

与逐行导入的区别：不关心列名精确映射，先把散落各 Sheet 的字符串值按企业聚成"信息池"，
再用枚举/关键词正则归一化：

```
excel_sheets_to_pool_read   Excel 多 Sheet → 信息池
                            pool[企业名key] = {name, code, raw_values[], sources[]}
                            （18 位信用代码自动识别；名称近似/厂房号剥离的多行合并）
pool_normalize              信息池 + 枚举值域 → 正则 → {字段: 值}（纯内存，拿不准留空不猜）
sql_enterprises_backfill    按 region（默认新能源园区）匹配库内企业 → 只补空（dry_run 默认 true）
```

正则覆盖：枚举精确命中、风险 bool 关键词、行业关键词、手机号、纯数字人数、地址关键词。
**正则的根本局限**：只能识别列名和枚举字面量，无法理解自由文本——
"香港独资算台资还是其他"、"数字是人数还是产值"、"联系人里谁是负责人谁是安全员"这类问题正则无解。

### 8.2 链路 B：知识库文档 → Agent 语义提取 → 强校验（单企业）

```
① rag_docs_search(企业名)              取企业简介/通讯录/经济指标台账等原文
② enterprise_field_schema_get         取允许提交的字段目录/枚举/类型（权威白名单）
③ Agent 自己阅读原文、按 schema 组装 {字段: 值} JSON（没有专门工具，Agent 本身即 LLM）
④ sql_enterprise_fields_commit        强校验 → 只补空 → dry_run 预览/写库；数组字段并集追加
```

**架构演进（重要）**：早期这一步由服务端模块 `agent_extract.py` 拼 prompt 调 deepseek API
完成提取（容器内直连 LLM）。该模块后来删除，"读原文 → 生成结构化 JSON"上移给调用方
Agent（Hermes），服务端只保留 [enterprise_fields.py](../app/services/svc_Import/enterprise_fields.py)
作为安全闸门：

- `get_field_schema()`：字段目录**不硬编码**，运行时从 `enterprise_writer.field_specs`
  + `Enterprise` 模型列类型动态推导（身份键跳过；有 `allowed`→枚举；value 看列类型分 bool/int；
  `string_array`→多值枚举）。SQL schema 改了，提示词与校验自动跟随，不会两处漂移。
- `validate_fields()`：白名单外字段丢弃、枚举值必须逐字命中、int 非负、bool 归一、
  多值数组逐项过滤去重保序，被拒字段及原因透明返回。

### 8.3 两种写库的边界区别

| | `sql_record_commit`（导入） | `sql_enterprises_backfill` / `sql_enterprise_fields_commit`（补全） |
|---|---|---|
| 新建企业 | 可以 | 不可以 |
| 已有值 | 按 merge 策略可更新 | 绝不覆盖（bool 已有 true 不补；数组只追加） |
| 适用 | 台账入库 | 空字段补全 |

---

## 九、历史补全实战：337 家新能源园区企业（2026-09）

> 以下为当时批次的数据快照，记录数据来源的覆盖边界与方法论结论。

**三层补全原则**：第一层查 Excel 台账 30 Sheet；第二层查 workfile 知识库
（企业简介/通讯录/经济指标）；第三层网络检索（当时明确暂缓）。

**阶段一（正则链路）净补成果**：

| 字段 | 补前空缺 | 补后空缺 | 净补 | 来源 |
|---|---|---|---|---|
| `industry` | 100 | 51 | 49 | Excel 行业关键词 + 信息池 |
| `asset_attribute` | 325 | 268 | 57 | RAG 企业简介 |
| `annual_output_value` | 337 | 285 | 52 | workfile 经济指标开票额（取整万元） |
| `person_in_charge` | 79 | 77 | 2 | 通讯录 + 简介联系人 |
| 风险 bool | — | — | 108 家次 | Excel 标签表 flag |

**阶段二（当时的 agent/LLM 语义链路，337 家全跑，0 报错，164 家写库）**：
写库分布 `fire_level` 72、`enterprise_category` 65、`industry` 37、
`has_hazardous_chemicals` 8、`asset_attribute` 9、`legal_person` 6、
`annual_output_value` 5、`confined_space` 5 等；对比纯正则的写库 96 家提升到 164 家。
最终空缺：`legal_person` 1、`industry` 14、`enterprise_category` 86、`fire_level` 243、
`asset_attribute` 259、`confined_space` 301、`annual_output_value` 280、
`employee_count` 172、`safety_officer` 130、`person_in_charge` 74。

**关键结论**：

1. Excel 层对基础信息（行业/地址/负责人/规模/人数）已饱和；标签类
   （消防等级/有限空间/资产属性）覆盖很弱——标签表企业与新能源园区主体重叠少。
2. workfile RAG 层能补的集中在有企业简介的约 27 家规上企业 + 通讯录/经济指标台账。
3. **本地数据源已挖尽，剩余缺口只能网络检索**（天眼查/企查查/信用中国）。
4. LLM 提取存在字段漂移（同企业多次跑个别枚举横跳），schema 校验能拦越界值，
   但"校验通过却是错的"无法自动发现，规上/高风险企业应抽样人工复核；
   后续可做 Agent 值与 SQL 值不一致时的冲突比对。

---

## 十、关键设计决策与踩坑备忘

| 事项 | 结论 |
|---|---|
| 判重键 | 优先 `code`，fallback `name`，再加归一化/相似/交叉验证共五级 |
| preview/commit 拆分 | Agent 在中间有控制权：审阅 changes、处理失败、逐企业决策 |
| L3 驱动方向 | 遍历 Agent 给的 fields 字典查 field_specs，不是遍历全表规格 |
| 失败隔离 | 单条 preview/commit 失败不卡死整批，失败行进批次报告 |
| 人员字段 | 安全员=安全负责人；姓名+电话合并同一字段，电话正则从混合值剥离 |
| 名称清洗 | 企业名末尾厂房号 `#编号` 剥离后匹配（"上海彩顺9#10#"类） |
| 行业双列 | 行业大类→`industry`；国民经济分类原文→extra |
| 消防名单 | 不跳过：按名匹配 → `fire_level=重点单位` |
| 事务策略 | 逐企业独立提交（失败行不阻塞整批）；未做整批事务回滚 |
| 明细列防护 | "是否"前缀精确匹配 bool，数量/介质/名称类明细一律进专属明细列或 extra |
