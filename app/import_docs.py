"""命令行批量导入工具 —— 从本地目录解析文档并写入 Qdrant。

索引流程（每个文件）:
    解析文档（PDF/Word/Excel/PPT/Markdown/TXT）
      → 文本分块（SentenceSplitter，可配置大小和重叠）
      → Dense 向量（BGE-M3 稠密嵌入 → Qdrant 主 collection）
      → Sparse 向量（BGE-M3 词权重 → Qdrant {collection}_sparse）

用法:
    # 追加导入（默认，不删除已有数据）
    python -m app.import_docs --dir /path/to/docs

    # 跳过已导入的文件（增量导入，避免重复工作）
    python -m app.import_docs --dir /path/to/docs --skip-existing

    # 强制重建（删除旧索引后重新导入，适合数据更新场景）
    python -m app.import_docs --dir /path/to/docs --replace

    # 仅当前目录（不递归子目录）
    python -m app.import_docs --dir /path/to/docs --no-recursive

    # 索引到指定 Qdrant collection（不传则用 QDRANT_COLLECTION 环境变量）
    python -m app.import_docs --dir /path/to/docs --collection my_custom_collection

退出码: 0=全部成功, 1=有文件导入失败
"""

import argparse
import logging
import sys
from pathlib import Path

from app.config import settings
from app.indexer import DocumentIndexer

# 配置日志格式：时间戳 + 级别 + 模块名 + 消息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    """批量导入主函数。

    流程:
        1. 解析命令行参数
        2. 创建 DocumentIndexer 实例
        3. 调用 index_directory() 执行批量索引
        4. 打印汇总统计（成功/跳过/失败数量）
        5. 返回退出码（0=全部成功, 1=有失败）
    """

    # ── 1. 解析命令行参数 ──────────────────────────────────
    parser = argparse.ArgumentParser(
        description="批量解析本地目录文档并写入 Qdrant 向量数据库"
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=settings.import_dir,
        help=f"待导入目录（默认: {settings.import_dir}）",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="不递归子目录（默认递归扫描所有子目录）",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="目标 Qdrant collection（默认: 使用 QDRANT_COLLECTION 环境变量）",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="跳过已导入的文件，只处理新文件（增量导入）",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="强制重建：先删除已有同源数据再重新索引（默认追加模式）",
    )
    args = parser.parse_args()

    # ── 2. 创建索引器并执行批量导入 ────────────────────────
    indexer = DocumentIndexer()
    result = indexer.index_directory(
        directory=args.dir,
        recursive=not args.no_recursive,
        replace=args.replace,
        skip_existing=args.skip_existing,
        collection=args.collection,
    )

    # ── 3. 统计分类 ──────────────────────────────────────
    duplicates = [f for f in result.files
                  if f.status == "skipped" and f.error and "已导入" in f.error]
    other_skips = [f for f in result.files
                   if f.status == "skipped" and not (f.error and "已导入" in (f.error or ""))]
    successes   = [f for f in result.files if f.status == "success"]
    failures    = [f for f in result.files if f.status == "failed"]

    # ── 4. 打印汇总 ──────────────────────────────────────
    print(f"\n{'=' * 50}")
    print(f"目录: {result.directory}")
    print(f"文件总数: {result.total_files}")
    print(f"✓ 新导入: {result.indexed}")
    print(f"⊘ 重复跳过: {len(duplicates)}")
    print(f"⊘ 其他跳过: {len(other_skips)}")
    print(f"✗ 失败: {result.failed}")
    print(f"写入 chunk 总数: {result.total_chunks}")
    print(f"{'=' * 50}")

    # ── 5. 新导入文件列表 ────────────────────────────────
    if successes:
        print(f"\n✅ 成功导入 ({len(successes)} 个):")
        for item in successes:
            print(f"    {item.path}  ({item.chunks} chunks)")

    # ── 6. 重复跳过列表（压缩显示）───────────────────────
    if duplicates:
        print(f"\n🔁 重复跳过 ({len(duplicates)} 个):")
        for item in duplicates:
            print(f"    {item.path}")

    # ── 7. 失败文件列表 ──────────────────────────────────
    if failures:
        print(f"\n❌ 导入失败 ({len(failures)} 个):")
        for item in failures:
            print(f"    {item.path}")
            if item.error:
                print(f"    ↳ 原因: {item.error}")

    # ── 8. 其他跳过详情 ──────────────────────────────────
    if other_skips:
        print(f"\n⚠️  其他跳过 ({len(other_skips)} 个):")
        for item in other_skips:
            print(f"    {item.path}")
            if item.error:
                print(f"    ↳ 原因: {item.error}")

    # 退出码：有失败返回 1，全成功返回 0（方便 CI/脚本判断）
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
