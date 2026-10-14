# Fuse + its fuse-agent connector binaries. Run with docker-compose.yml
# (./install.sh --docker); config comes from the environment, never baked in.

FROM golang:1.25-bookworm AS windows-agent
WORKDIR /src/agent
COPY agent/ .
RUN make -C windows dist

FROM python:3.12-slim-bookworm AS linux-agent
RUN apt-get update && apt-get install -y --no-install-recommends make gcc libc6-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/agent
COPY agent/ .
RUN make

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=linux-agent /src/agent/dist/ agent/dist/
COPY --from=windows-agent /src/agent/dist/ agent/dist/
EXPOSE 8088
CMD ["scripts/docker-entrypoint.sh"]
