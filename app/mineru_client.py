"""MinerU PDF 解析 HTTP 客户端。

MinerU 是一个基于深度学习的 PDF 解析引擎，可以：
- 将扫描件 PDF 转为 Markdown（通过 OCR）
- 提取公式、表格、图片等结构化内容

这里封装 HTTP 调用，与 Docker 中的 MinerU 容器通信。

大 PDF 处理：
- 超过 MINERU_PAGE_CHUNK_SIZE 页的 PDF 自动按页拆分
- 每批独立发送 MinerU，结果合并，避免单次处理 OOM

显存管理策略：
- 每次调用前通过 nvidia-smi 检测 GPU 显存占用
- 超过阈值（默认 65%）时主动重启 MinerU + 卸载 Ollama
- 同时设一个兜底上限：每 N 个 PDF 无论显存如何都重启
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter

from app.config import settings

logger = logging.getLogger(__name__)

# ── PDF 分片 ──────────────────────────────────────────────

# 每批处理的页数，超过此阈值自动拆分（0=不拆分）
_PAGE_CHUNK_SIZE = int(os.getenv("MINERU_PAGE_CHUNK_SIZE", "20"))

# 动态缩小的最小分片：失败时从 CHUNK_SIZE 逐次折半，到此为止
_MIN_CHUNK = int(os.getenv("MINERU_MIN_CHUNK_SIZE", "5"))

# 分片处理时每处理 N 个分片主动重启 MinerU（防 Paddle 显存碎片）
_CHUNK_RESTART_EVERY = int(os.getenv("MINERU_CHUNK_RESTART_EVERY", "5"))

# ── MinerU 重启与重试 ──────────────────────────────────────

# 显存阈值：GPU 总显存使用比例超过此值就主动重启 MinerU
_VRAM_THRESHOLD = float(os.getenv("MINERU_VRAM_THRESHOLD", "0.65"))

# 兜底上限：即使显存没超阈值，每 N 个 PDF 也强制重启（防内存碎片等隐性泄漏）
_RESTART_EVERY_N = int(os.getenv("MINERU_RESTART_EVERY_N", "10"))

_mineru_call_count = 0        # 计数器
_mineru_lock = threading.Lock()
_socket_available = True      # Docker socket 不可用时跳过重启
_nvml_available = True        # nvidia-smi 不可用时跳过显存检测


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
        httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={"model": settings.ollama_default_model, "keep_alive": 0},
            timeout=10,
        )
        logger.info("Ollama 模型已卸载（释放 ~5.7GB 显存）")
    except Exception as e:
        logger.warning("暂停 Ollama 失败，继续执行: %s", e)


def _get_gpu_memory_mb() -> tuple[int, int]:
    """通过 nvidia-smi 查询 GPU 显存，返回 (used_mb, total_mb)。

    失败时返回 (-1, -1)，调用方应降级为不检测。
    """
    global _nvml_available
    if not _nvml_available:
        return (-1, -1)

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("nvidia-smi 返回空")
        used, total = result.stdout.strip().split(",")
        return (int(used.strip()), int(total.strip()))
    except FileNotFoundError:
        logger.warning("nvidia-smi 不可用，跳过显存检测")
        _nvml_available = False
        return (-1, -1)
    except Exception as e:
        logger.warning("查询 GPU 显存失败: %s", e)
        return (-1, -1)


def _should_restart_for_vram() -> bool:
    """检测当前 GPU 显存占用是否超过阈值，需要重启 MinerU。"""
    used_mb, total_mb = _get_gpu_memory_mb()
    if used_mb < 0 or total_mb < 0:
        return False  # 无法检测，不触发重启

    ratio = used_mb / total_mb
    if ratio >= _VRAM_THRESHOLD:
        logger.info(
            "GPU 显存 %d/%d MB (%.0f%%) ≥ 阈值 %.0f%%，触发重启",
            used_mb, total_mb, ratio * 100, _VRAM_THRESHOLD * 100,
        )
        return True

    logger.debug("GPU 显存 %d/%d MB (%.0f%%)，正常", used_mb, total_mb, ratio * 100)
    return False


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


def _get_pdf_page_count(path: Path) -> int:
    """快速读取 PDF 页数（不解析内容，很快）。"""
    with path.open("rb") as f:
        reader = PdfReader(f)
        return len(reader.pages)


def _create_chunk_pdf(path: Path, start: int, end: int) -> Path:
    """从 PDF 提取指定页范围 [start, end) 为临时文件。"""
    f = path.open("rb")
    try:
        reader = PdfReader(f)
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
    finally:
        f.close()

    temp_dir = Path(tempfile.gettempdir()) / f"mineru_chunks_{os.getpid()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = temp_dir / f"{path.stem}_p{start + 1}-p{end}.pdf"
    with chunk_path.open("wb") as out:
        writer.write(out)
    return chunk_path


def parse_pdf_with_mineru(path: Path) -> str:
    """调用 MinerU HTTP API 解析 PDF，返回 Markdown 格式文本。

    大 PDF 处理：页数超过 MINERU_PAGE_CHUNK_SIZE 时自动按页拆分，
    每批独立发送 MinerU 处理，结果合并。

    显存管理：
    1. 每次调用前检测 GPU 显存占用，超过阈值自动重启 MinerU
    2. 兜底：每 N 个 PDF 强制重启（防隐性泄漏）
    3. 出错时（OOM / 连接断开等）重启并重试一次

    参数:
        path: PDF 文件的本地路径

    返回:
        str: 解析后的 Markdown 文本

    Raises:
        MinerUError: 服务请求失败或返回无效内容
    """
    # ── 判断是否需要分片 ─────────────────────────────────
    if _PAGE_CHUNK_SIZE > 0:
        page_count = _get_pdf_page_count(path)
        if page_count > _PAGE_CHUNK_SIZE:
            return _parse_with_chunks(path, page_count)

    return _parse_single_pdf(path)


def _parse_single_pdf(path: Path) -> str:
    """单个 PDF 完整处理（带显存检测+重试）。"""
    global _mineru_call_count

    # ── 释放嵌入模型显存，给 MinerU 腾空间 ────────────────
    from app.models import offload_models_to_cpu
    offload_models_to_cpu()

    # ── 显存检测：超过阈值则主动重启 ──────────────────────
    with _mineru_lock:
        _mineru_call_count += 1
        current_count = _mineru_call_count

    hit_cap = _RESTART_EVERY_N > 0 and current_count % _RESTART_EVERY_N == 0
    need_restart = hit_cap or _should_restart_for_vram()

    if need_restart:
        reason = "兜底上限" if hit_cap else "显存阈值"
        logger.info("MinerU 主动重启（%s，第 %d 个 PDF）", reason, current_count)
        _stop_ollama()
        _restart_mineru()
        _wait_mineru_ready()

    result = _call_parse(path)

    # ── 出错时重启重试 ────────────────────────────────────
    if isinstance(result, MinerUError):
        msg = str(result).lower()
        retryable = (
            "out of memory" in msg
            or "OutOfMemory" in msg
            or "connection refused" in msg
            or "connection failed" in msg
            or "server disconnected" in msg
            or "timed out" in msg
        )
        if retryable:
            logger.warning("MinerU 错误（%s），重启并重试: %s", msg[:80], path.name)
            _stop_ollama()
            if _restart_mineru():
                _wait_mineru_ready()
                result = _call_parse(path)

    if isinstance(result, Exception):
        raise result

    return result


def _parse_with_chunks(path: Path, total_pages: int) -> str:
    """分片处理大 PDF：按需创建分片 → 逐个解析 → 合并结果。

    动态缩小：单个分片失败（含重试）后，将该页范围以折半后的 chunk_size
    重新处理，直到 MINERU_MIN_CHUNK_SIZE 为止。

    定期重启：每 CHUNK_RESTART_EVERY 个分片主动重启 MinerU，释放 Paddle 显存碎片。
    """
    from app.models import offload_models_to_cpu
    offload_models_to_cpu()

    # ── 任务队列：[(start, end, chunk_size), ...] ─────────
    chunk_size = _PAGE_CHUNK_SIZE
    tasks: list[tuple[int, int, int]] = []
    pos = 0
    while pos < total_pages:
        end = min(pos + chunk_size, total_pages)
        tasks.append((pos, end, chunk_size))
        pos = end

    results: list[tuple[int, str]] = []  # (start_page, markdown)
    chunk_index = 0

    while tasks:
        start, end, size = tasks.pop(0)
        pages = end - start
        chunk_index += 1
        chunk_path = _create_chunk_pdf(path, start, end)

        logger.info("分片 %d/%d: %s_p%d-%d (%d页, chunk=%d)",
                     chunk_index, chunk_index + len(tasks), path.stem,
                     start + 1, end, pages, size)

        success = False
        try:
            md = _parse_single_pdf(chunk_path)
            results.append((start, md))
            success = True
        except MinerUError as e:
            # 重启 MinerU 后重试一次
            logger.warning("分片 %d 失败: %.80s，重启后重试", chunk_index, str(e))
            _stop_ollama()
            if _restart_mineru() and _wait_mineru_ready():
                try:
                    md = _parse_single_pdf(chunk_path)
                    results.append((start, md))
                    success = True
                    logger.info("分片 %d 重试成功", chunk_index)
                except MinerUError:
                    pass

            if not success:
                if size > _MIN_CHUNK:
                    new_size = max(size // 2, _MIN_CHUNK)
                    logger.warning("分片 %d 缩至 %d 页重试 (页 %d-%d)",
                                   chunk_index, new_size, start + 1, end)
                    # 将该页范围按新大小重新排队
                    sub_tasks: list[tuple[int, int, int]] = []
                    p = start
                    while p < end:
                        sub_end = min(p + new_size, end)
                        sub_tasks.append((p, sub_end, new_size))
                        p = sub_end
                    tasks = sub_tasks + tasks
                else:
                    raise MinerUError(
                        f"页 {start + 1}-{end} 在最小分片 {_MIN_CHUNK} 页下仍失败"
                    )
        finally:
            chunk_path.unlink(missing_ok=True)

        # ── 定期重启释放 Paddle 显存碎片 ─────────────────
        if success and chunk_index % _CHUNK_RESTART_EVERY == 0 and tasks:
            logger.info("分片 %d: 主动重启 MinerU（每 %d 分片）",
                         chunk_index, _CHUNK_RESTART_EVERY)
            _stop_ollama()
            _restart_mineru()
            _wait_mineru_ready()

    # ── 按起始页排序合并 ──────────────────────────────────
    results.sort(key=lambda x: x[0])
    logger.info("分片合并: %s (%d 页 → %d 个结果)", path.name, total_pages, len(results))
    return "\n\n".join(r[1] for r in results)


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
