# Mirrors and GitHub releases

## When the defaults are blocked

`mirrors.yml` changes where `outpost fetch` downloads from. Uploads to Gitea keep the normal names.

```yaml
pypi: https://pypi.mirror.example/simple
npm: https://npm.mirror.example
docker: https://docker.mirror.example         # must speak the registry v2 API
debian: http://deb.mirror.example/debian
ubuntu: http://archive.mirror.example/ubuntu
github: https://gh.mirror.example             # rewrites github.com release links
```

## Boxes as GitHub releases

GitHub release assets stay forever and may be up to 2 GB each. `outpost pack` splits the box
by kind so no file is too large, and signs the manifest:

```
outpost pack --out dist
# dist/box-images-2026-10-26.tar.xz, box-pypi-..., box-npm-..., box-apt-..., box-files-..., box-manifest-...
```

The workflow in `.github/workflows/fetch.yml` runs `fetch` and `pack` on a schedule in a fork
of this repository that holds your workspace, and attaches the files to a release.
Download them anywhere and upload:

```
outpost upload dist/*.tar.xz
```

`upload` verifies `manifest.sig` against `pki/box.pub` when both exist. Keep `secrets/box.key`
in the workflow's secrets, never in the repository.

## Moving a box by hand

The box is a folder. `scp -r box/ laptop2:` or a USB stick both work; `outpost upload` reads
whatever folder you point it at and checks every file against `manifest.json` first.
