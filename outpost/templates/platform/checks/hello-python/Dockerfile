# The only change from a normal Dockerfile: copy Outpost's config files in first.
ARG GITEA
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config
FROM python:3.12-slim
COPY --from=config / /
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask
COPY app.py /app.py
CMD ["python", "/app.py"]
