# Use the requested uv base image with Python 3.12 (pyproject.toml requires-python >=3.12)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
    && useradd --system --gid 999 --uid 999 --create-home nonroot

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Then, add the rest of the project source code and install it
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Cron (컨테이너 내장 스케줄러 - docker-compose의 investwarning-collector-cron 서비스에서 사용)
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Seoul
# ENV TZ는 cron 데몬(crond) 자체가 crontab 시각을 해석하는 기준에는 반영되지 않음 -
# crond는 /etc/localtime을 봄. 이걸 안 하면 crontab에 적어둔 시각이 KST가 아니라
# UTC로 해석돼 실제 발화 시각이 9시간 어긋날 수 있음(docker_guide.md §5).
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY docker/crontab /etc/cron.d/investwarning-collector-cron
RUN chmod 0644 /etc/cron.d/investwarning-collector-cron \
    && chmod +x /app/docker/run-daily.sh /app/docker/cron-entrypoint.sh

# Change ownership of the app directory to nonroot user
RUN chown -R nonroot:nonroot /app

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

# Use the non-root user to run our application (cron 서비스는
# docker-compose.yml에서 user: root로 오버라이드 - cron 데몬 기동에 root 필요)
USER nonroot

# Default command
CMD ["python", "cli.py", "--help"]
