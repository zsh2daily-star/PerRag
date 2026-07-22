"""对话历史持久化模块 —— SQLite 存储，跨重启/跨前端保留对话上下文。

每个会话有一个 session_id，对应一个 messages JSON 数组。
客户端（Hermes/Open WebUI）在请求中携带 session_id。
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 数据库文件路径（放在项目根目录，容器挂载后会持久化）
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "conversations.db"


def _ensure_db() -> None:
    """确保数据库和表存在。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            model TEXT DEFAULT '',
            messages_json TEXT DEFAULT '[]',
            created_at REAL,
            updated_at REAL
        )
    """)
    conn.commit()
    conn.close()


# 写锁（SQLite 单写，但多线程并发 insert/update 需要串行化）
_write_lock = threading.Lock()


def load_conversation(session_id: str) -> list[dict] | None:
    """加载一个会话的消息历史。

    参数:
        session_id: 会话 ID（由客户端生成，如 Hermes/Open WebUI 传入）

    返回:
        list[dict]: OpenAI 格式的消息列表 [{"role":"user","content":"..."}, ...]
        None: 会话不存在
    """
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT messages_json FROM conversations WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return []


def save_conversation(session_id: str, messages: list[dict], model: str = "") -> None:
    """保存或更新会话消息（UPSERT —— insert or update）。

    参数:
        session_id: 会话 ID
        messages: OpenAI 格式的完整消息列表
        model: 使用的 LLM 模型名（记录用途，可选）

    如果 session_id 已存在则覆盖 messages 和 updated_at，
    否则新建一行并记录 created_at 和 updated_at。
    """
    _ensure_db()
    now = time.time()
    messages_json = json.dumps(messages, ensure_ascii=False)

    with _write_lock:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO conversations (session_id, model, messages_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                model = excluded.model,
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
        """, (session_id, model, messages_json, now, now))
        conn.commit()
        conn.close()


def delete_conversation(session_id: str) -> bool:
    """删除一个会话。返回 True 表示删除成功。"""
    _ensure_db()
    with _write_lock:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
    return deleted


def list_conversations() -> list[dict]:
    """列出所有会话（返回摘要，不含完整消息）。"""
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT session_id, model, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "session_id": row[0],
            "model": row[1],
            "created_at": row[2],
            "updated_at": row[3],
        }
        for row in rows
    ]


# ── 自动修剪 ──────────────────────────────────────────────

MAX_MESSAGES_PER_CONVERSATION = 50  # 单会话最大消息数，超出则删除最早的非 system 消息


def append_and_save(
    session_id: str,
    new_messages: list[dict],
    model: str = "",
    max_messages: int = MAX_MESSAGES_PER_CONVERSATION,
) -> list[dict]:
    """追加新消息到会话并持久化，超限自动修剪。

    参数:
        session_id: 会话 ID
        new_messages: 新增的消息列表（如 user + assistant 两条）
        model: 使用的模型名
        max_messages: 最多保留多少条消息，超出则删除最早的

    返回:
        list[dict]: 修剪后的完整消息列表
    """
    existing = load_conversation(session_id) or []
    merged = existing + new_messages

    # 超过上限时修剪最旧的非 system 消息
    if len(merged) > max_messages:
        system_msgs = [m for m in merged if m.get("role") == "system"]
        non_system = [m for m in merged if m.get("role") != "system"]
        keep = non_system[-(max_messages - len(system_msgs)):]
        merged = system_msgs + keep
        logger.info("会话 %s 触发修剪: %d → %d 条", session_id, len(existing) + len(new_messages), len(merged))

    save_conversation(session_id, merged, model=model)
    return merged
