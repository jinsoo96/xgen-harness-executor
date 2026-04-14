FROM python:3.12-slim

WORKDIR /app

# xgen_harness 라이브러리 복사
COPY xgen_harness/ /app/xgen_harness/
COPY pyproject.toml /app/

# 의존성 설치
RUN pip install --no-cache-dir httpx fastapi uvicorn websockets

# 백엔드 복사
COPY web/backend/main.py /app/web/backend/main.py

WORKDIR /app/web/backend

EXPOSE 8088
CMD ["python", "main.py"]
