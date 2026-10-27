# Two changes from a normal Dockerfile: the base image is named by its Gitea copy, and
# Outpost's config files are copied in first.
ARG GITEA
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config
FROM ${GITEA}/${ORG}/library--python:3.12-slim
COPY --from=config / /
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask
COPY app.py /app.py
CMD ["python", "/app.py"]
