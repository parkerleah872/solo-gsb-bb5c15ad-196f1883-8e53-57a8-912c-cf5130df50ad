FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码
COPY app ./app

EXPOSE 8000

# 容器启动即完成全部依赖装配；由 uvicorn 直接提供服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
