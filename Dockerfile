# ── RAG FastAPI 应用镜像 ──────────────────────────────────
# 构建: docker build -t rag-api .
# 运行: docker-compose up -d

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（pypdf 等纯 Python 库不需要系统依赖，
# 但 huggingface/transformers 可能需要这些）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    antiword \
    && rm -rf /var/lib/apt/lists/*

# 使用国内 PyPI 镜像加速（避免连接 pypi.org 超时）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/

# 创建数据目录（挂载点）
RUN mkdir -p /app/data/uploads /app/data/mineru /app/models

# 暴露 FastAPI 端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]