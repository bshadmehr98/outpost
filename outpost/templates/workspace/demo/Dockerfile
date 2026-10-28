# The Outpost-specific lines are the first five: the config image carries pip.conf, apt sources,
# npm config and the CA, so every install below is answered by Gitea; and the base image is named
# by its Gitea copy (python:3.12-slim lives under ${ORG} as library/python). A bare
# `FROM python:3.12-slim` would ask Docker Hub, which a sealed node rejects at once.
ARG GITEA=gitea.internal
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config

FROM ${GITEA}/${ORG}/library/python:3.12-slim
COPY --from=config / /
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .

# Who built this and from what; shown on the page.
ARG GITEA
ARG GIT_SHA=unknown
ENV GITEA=${GITEA} GIT_SHA=${GIT_SHA}

# Build-time proof: try the public internet from inside the build. The build FAILS if any of it
# is reachable, so an image that exists was built with the internet blocked. The results are
# baked into the image and shown on the page.
RUN python probe.py --write build-proof.json --fail-if-reachable

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
