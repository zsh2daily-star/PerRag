"""MinerU PDF 解析 HTTP 客户端。

MinerU 是一个基于深度学习的 PDF 解析引擎，可以：
- 将扫描件 PDF 转为 Markdown（通过 OCR）
- 提取公式、表格、图片等结构化内容

这里封装 HTTP 调用，与 Docker 中的 MinerU 容器通信。
"""

import logging
import threading
import time
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── MinerU 重启与重试 ──────────────────────────────────────

# MinerU v0.8.1 不会在每次解析后释放 GPU 显存，累积多轮后 OOM。
# 这里在 OOM 时自动重启 MinerU 容器并重试，同时每 N 个 PDF 主动重启。
_RESTART_EVERY_N = 0         # 0=不主动重启，仅 OOM 时重启
_mineru_call_count = 0        # 计数器
_mineru_lock = threading.Lock()
_socket_available = True      # Docker socket 不可用时跳过重启


def _restart_mineru() -> bool:
    """通过 Docker socket 重启 MinerU 容器。返回 True 表示成功。"""
    global _socket_available
    if not _socket_available:
        return False

    try:
        transport = httpx.HTTPTransport(uds="/var/run/docker.sock")
        with httpx.Client(transport=transport, timeout=30) as client:
            resp = client.post("http://localhost/containers/mineru/restart")
            if resp.status_code == 204:
                logger.info("MinerU 容器已重启")
                return True
            else:
                logger.warning("MinerU 重启返回 %d: %s", resp.status_code, resp.text[:200])
                return False
    except Exception as e:
        logger.warning("无法访问 Docker socket，跳过 MinerU 重启: %s", e)
        _socket_available = False
        return False


def _stop_ollama() -> None:
    """暂停 Ollama 释放显存。"""
    try:
        # Ollama 的 keep_alive=0 会让模型在当前请求完成后立即卸载
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_default_model, "keep_alive": 0},
            timeout=10,
        )
        logger.info("Ollama 模型已卸载（释放 ~5.7GB 显存）")
    except Exception as e:
        logger.warning("暂停 Ollama 失败，继续执行: %s", e)


def _wait_mineru_ready(timeout: int = 60) -> bool:
    """等待 MinerU 服务就绪。"""
    url = f"{settings.mineru_base_url}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


# ── 公共接口 ──────────────────────────────────────────────


class MinerUError(Exception):
    """MinerU 服务调用异常。"""
    pass


def check_health() -> bool:
    """检查 MinerU 服务是否正常运行。"""
    url = f"{settings.mineru_base_url}/"
    try:
        response = httpx.get(url, timeout=10)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def parse_pdf_with_mineru(path: Path) -> str:
    """调用 MinerU HTTP API 解析 PDF，返回 Markdown 格式文本。

    OOM 时自动重启 MinerU 容器并重试一次。每 N 个 PDF 主动重启防止显存累积。

    参数:
        path: PDF 文件的本地路径

    返回:
        str: 解析后的 Markdown 文本

    Raises:
        MinerUError: 服务请求失败或返回无效内容
    """
    global _mineru_call_count

    # 主动重启（仅 _RESTART_EVERY_N > 0 时启用）
    with _mineru_lock:
        _mineru_call_count += 1
        should_restart = (
            _RESTART_EVERY_N > 0 and _mineru_call_count % _RESTART_EVERY_N == 0
        )

    if should_restart:
        logger.info("MinerU 已处理 %d 个 PDF，主动重启释放显存", _mineru_call_count)
        _restart_mineru()
        _wait_mineru_ready()

    result = _call_parse(path)

    # OOM 时重启并重试
    if isinstance(result, MinerUError) and (
        "out of memory" in str(result).lower() or "OutOfMemory" in str(result)
    ):
        logger.warning("MinerU OOM，尝试暂停 Ollama 并重启: %s", path.name)
        _stop_ollama()
        if _restart_mineru():
            _wait_mineru_ready()
            result = _call_parse(path)

    if isinstance(result, Exception):
        raise result

    return result


def _call_parse(path: Path) -> str | Exception:
    """单次 MinerU 解析调用（不含重试逻辑）。"""
    url = f"{settings.mineru_base_url}/pdf_parse"
    logger.info("MinerU 解析: %s -> %s", path.name, url)

    try:
        with path.open("rb") as file_handle:
            response = httpx.post(
                url,
                files={"pdf_file": (path.name, file_handle, "application/pdf")},
                params={
                    "parse_method": "auto",
                    "is_json_md_dump": False,
                    "output_dir": "/tmp/mineru",
                },
                timeout=settings.mineru_timeout,
            )
    except httpx.HTTPError as e:
        return MinerUError(f"MinerU 连接失败: {e}")

    if response.status_code != 200:
        return MinerUError(
            f"MinerU 请求失败 ({response.status_code}): {response.text[:500]}"
        )

    payload = response.json()
    error = payload.get("error", "")
    if error:
        return MinerUError(error)

    md_content = payload.get("md_content") or payload.get("text") or ""
    if not md_content.strip():
        return MinerUError(f"MinerU 未返回有效内容: {path.name}")

    return md_content.strip()
