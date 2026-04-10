# Multi-stage build for xgen-harness-executor
FROM rust:1.83-slim AS builder

WORKDIR /app

# 의존성만 먼저 빌드 (캐시)
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release 2>/dev/null || true
RUN rm -rf src

# 소스 복사 + 빌드
COPY src/ src/
RUN cargo build --release

# 런타임 이미지
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/target/release/xgen-harness-executor /app/xgen-harness-executor
COPY bridge/ /app/bridge/

ENV BIND_ADDR=0.0.0.0:8000
ENV RUST_LOG=info
ENV NODE_BRIDGE_SCRIPT=/app/bridge/server.py

EXPOSE 8000

CMD ["/app/xgen-harness-executor"]
