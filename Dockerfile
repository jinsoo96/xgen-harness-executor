# xgen-harness-stdio 바이너리 배포용
# Docker 안에서 Rust 빌드 하지 않음 — 사전 빌드된 바이너리만 복사
#
# 사용법:
#   1. 로컬에서 빌드: cargo build --release --bin xgen-harness-stdio -j 2
#   2. docker compose up --build
#
# 빌드된 바이너리가 없으면 컨테이너 시작 시 에러 메시지 출력

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 사전 빌드된 바이너리 복사
COPY target/release/xgen-harness-stdio /usr/local/bin/xgen-harness-stdio

CMD ["echo", "harness-stdio binary ready"]
