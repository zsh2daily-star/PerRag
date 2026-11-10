"""业务服务层 —— 按职责分 5 个子包：

    svc_Auth/      认证与安全：user_service（账号/密码）、auth_service（JWT）、audit_service（审计日志）
    svc_Ledger/    台账业务：enterprise / inspection / factory_in_factory / material / dashboard
    svc_Import/    Excel 导入与补全管道：import_parser / import_writer / backfill_service / enterprise_fields
    svc_DocExport/ 文书导出：按模板填充生成 .docx（交办单等交付文书），供 router 与 MCP 共用
    svc_Common/    通用引擎：filter_service + filter_helpers（筛选条件树 → SQL，多表共用）
"""
