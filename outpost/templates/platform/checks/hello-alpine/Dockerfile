# The same two changes as hello-python, on an Alpine base: the image is named by its Gitea copy, and
# Outpost's config files are copied in first, so `apk add` is answered by Gitea's Alpine registry.
ARG GITEA
ARG ORG=pkgs
FROM ${GITEA}/${ORG}/config:latest AS config
FROM ${GITEA}/${ORG}/library--alpine:3.20
COPY --from=config / /
RUN apk add --no-cache curl
CMD ["curl", "--version"]
