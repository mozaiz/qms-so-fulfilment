# QMS — SO Fulfilment
#
# Single container, SQLite inside a volume. Nothing else to run.
#
# NOTE: not exercised in this project's environment (no Docker available on the
# dev box) — treat as convenience, and prefer install.sh on Linux/macOS.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    QMS_PORT=8099 \
    QMS_DB=/data/qms.db \
    QMS_TZ=Asia/Kuala_Lumpur

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py run.sh ./
COPY static ./static

RUN mkdir -p /data

EXPOSE 8099
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/api/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "./run.sh"]
