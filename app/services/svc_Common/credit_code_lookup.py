"""企业信用代码网络查询 —— 多源策略（WebSearch 主 + 天眼查/启信宝辅）。

数据源优先级：
  1. WebSearch（最可靠）：搜索"{企业名} 统一社会信用代码"，
     摘要中直接包含信用代码，多源交叉验证（天眼查/企查查/启信宝/水滴信用）。
  2. WebFetch 天眼查：https://www.tianyancha.com/search?key=企业名
     WebFetch 能渲染 JS，对大中型企业效果好。
  3. WebFetch 启信宝：https://www.qixin.com/search?key=企业名
     备用源，部分小企业可能只有导航菜单。

注意：本模块只提供查询模板和 URL 构建器，实际网络请求由 Trae 端
WebSearch/WebFetch MCP 工具完成（容器外运行），不在 Docker 容器内直接调用。
"""
import re
from collections import Counter
from typing import Optional
from urllib.parse import quote

# ── 数据源 URL 模板 ──────────────────────────────────────
_QIXIN_SEARCH = "https://www.qixin.com/search?key={}"
_TIANYANCHA_SEARCH = "https://www.tianyancha.com/search?key={}"

# 统一社会信用代码正则：18 位字母数字，9 开头（企业）
_CODE_RE = re.compile(r"9[0-9A-Z]{17}")

# 完整格式校验正则（首字符 9，共 18 位字母数字）
_CODE_FULL_RE = re.compile(r"^[0-9A-Z]{18}$")


def extract_credit_code(text: str) -> Optional[str]:
    """从文本中提取统一社会信用代码。

    从 WebSearch/WebFetch 返回的文本中正则匹配 18 位信用代码（9 开头）。
    多个匹配取频率最高（多源交叉验证时同一代码会出现多次）。

    Args:
        text: 包含信用代码的文本（如 WebSearch 结果 / WebFetch 页面内容）

    Returns:
        信用代码字符串，或 None（未找到）
    """
    if not text:
        return None
    matches = _CODE_RE.findall(text)
    if not matches:
        return None
    if len(matches) > 1:
        return Counter(matches).most_common(1)[0][0]
    return matches[0]


def validate_credit_code(code: str) -> bool:
    """校验信用代码格式（18 位、9 开头）。"""
    if not code:
        return False
    code = code.strip().upper()
    return bool(_CODE_FULL_RE.match(code)) and code.startswith("9")


def build_search_query(enterprise_name: str) -> str:
    """构建 WebSearch 查询语句。

    WebSearch 对"{企业名} 统一社会信用代码"查询效果最好：
    搜索结果摘要中直接包含信用代码，且多源（天眼查/企查查/启信宝/水滴信用）交叉验证。
    """
    return f"{enterprise_name.strip()} 统一社会信用代码"


def build_tianyancha_url(enterprise_name: str) -> str:
    """构建天眼查搜索 URL（供 WebFetch 抓取）。"""
    return _TIANYANCHA_SEARCH.format(quote(enterprise_name.strip()))


def build_qixin_url(enterprise_name: str) -> str:
    """构建启信宝搜索 URL（备用源，供 WebFetch 抓取）。"""
    return _QIXIN_SEARCH.format(quote(enterprise_name.strip()))
