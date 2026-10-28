# Target map for the cleanup

This page is the plan the refactor agents follow. It says where every module and function of
today's `outpost/` goes, what the new names are, and in which order the work happens. Only the code
inside changes. The CLI commands, flags, output wording, the workspace layout on disk, file formats
and the `servers.yml` schema stay the same. One exception: help text says "workspace" instead of
"platform folder".

Read first: [style guide](../development/style.md), [glossary](../glossary.md), `outpost/ssh/`
(the model package).

## 0. Rules every step follows

| rule | what it means |
|---|---|
| **Layers** | A module imports only from its own layer or the layers below it. Layer 0: `ssh`, `versions`, `http`, `workspace`, `config`, `distros`. Layer 1: `system`, `endpoints`, `needs`, `box`, `pki`, `oci`, `fetch`, `hosts`, `egress`, `hostsfile`, `client_config`. Layer 2: `gitea`. Layer 3: `infra`, `checks`, `demo`. Layer 4: `report`, `ui`, `lab`, `rotate_ca`, `cli`. This removes today's lazy imports that work around cycles (`cli._proxy_info`, `verify.proxy_checks`, `proxy.host_checks`, ...). |
| **`system` or plain values** | A function that takes `(platform, servers)` today takes `system: AirGappedSystem`. A function that takes plain values (`box: Path`, `mirrors: Mirrors`, `host`, `ctx`) keeps them. Lower-level code stays easy to test. |
| **Only `report`, `ui` and `lab` take a `Workspace`** | They must work while `servers.yml` is missing or broken: the dashboard edits it, the report shows a half-built system, and `lab up` writes it. |
| **Hosts come from the system** | Code gets a `Host` from `system.host(alias)`, `system.gitea_host`, `system.nodes(name)` or `system.proxy_host`. Nothing outside `system.py` calls `Host(...)`, except `ui` (its own connect timeout) and `lab`. Tests patch one name: `outpost.system.Host`. |
| **Output** | Modules report progress through a `log=print` parameter, as today. Only `outpost/cli/` prints, through `cli/output.py`. |
| **Splits** | Every `# noqa: C901` function, and every function over about 50 lines, becomes named steps. The noqa goes away. |
| **Renames** | No aliases. A rename updates every caller and every test (unit and integration) in the same step. |

## 1. Target tree

`(new)` means a new file. `<-` shows where the code comes from today.

```
outpost/
  __init__.py            __version__                                      <- same
  __main__.py            python -m outpost -> cli.main                    <- same
  workspace.py           Workspace and the layout's names                 <- paths.py
  config/                what the user writes: servers.yml, mirrors.yml   <- config.py
    __init__.py            overview, re-exports
    errors.py              ConfigError
    yaml_file.py           read_yaml_mapping()                            <- config._read_yaml
    servers.py             GiteaConfig, InfraConfig, Servers, load_servers(), INFRA_TYPES
    egress_proxy.py        ProxyConfig, load_egress_proxy(), proxy_allow_entry(), HOSTNAME
    mirrors.py             Mirrors, load_mirrors()
  system.py        (new) AirGappedSystem: the context object (section 2)
  endpoints.py     (new) GiteaEndpoint, ProxyEndpoint, and how to work them out
                         <- infra/clients.GiteaInfo, ProxyInfo; gitea/connect.gitea_info;
                            infra/proxy.info; cli._proxy_info, _proxy_info_or_warn, _cached_facts
  distros.py             distro names and families, in one place: DEBIAN, UBUNTU, RPM_IMAGES, RPM_OS_IDS,
                         os_family, apk_branch_of, rpm_distro_of_host, distro_of_host, kind_for_distro,
                         split_rpm_distro, is_apt_distro, is_apk_branch, apk_branch_version
                         <- fetch/apt DEBIAN/UBUNTU, fetch/rpm split/distro_for_host/OS_IDS,
                            infra/common os_family/apk_branch_of, ui/projects kind_for_distro
  versions.py            pinned versions, pinned_files() in named steps, arch_names(), use_arch()  <- same
  http.py                Http, Response, HttpError, sha256_file; retries only GET and HEAD;
                         a Path body is streamed from disk                <- same
  needs/                 what projects need, merged into one Needs
    __init__.py            overview, needs file format, file map, re-exports
    model.py               FileNeed, NpmNeed, Needs, add_unique          <- needs/__init__
    needs_file.py          load_needs(), read_needs_file(): _read_images, _read_pypi, _read_npm,
                           _read_os_packages, _read_files                 <- needs/__init__._parse_one, load_needs
    pinned.py              all_needs(system), add_pinned_files(), add_proxy_package(), proxy_package_need(),
                           PROXY_PACKAGE
                           <- needs.add_platform_files, infra/proxy.add_package/package_need/PACKAGE, cli._add_proxy_package
    python_lockfiles.py    read_python_lockfile(); parse_requirements, poetry, uv, Pipfile  <- needs/parsers.py (Python half)
    js_lockfiles.py        read_js_lockfile(); parse_package_lock, pnpm, yarn               <- needs/parsers.py (JS half)
  box.py                 the box folder, its manifest, pack/unpack, check_signature()   <- same
  pki.py                 CA, Gitea certificate, box signing key (openssl); takes a Workspace  <- same
  oci/                   container images over plain HTTP                  <- oci.py
    __init__.py            overview, file map, re-exports
    refs.py                Ref, parse_ref, gitea_ref, original_ref, GITEA_IMAGE_NAME
    registry.py            Registry: pull (_get_manifest, _pick_arch, _fetch_blobs), push (blobs from disk) -> Pushed(digest, uploaded), tokens
    layout.py              build_layout, layout_ref, digest_of, write_index(), blob_path
    media_types.py         MANIFEST_TYPES, INDEX_TYPES, ACCEPT, REF_ANNOTATION
  fetch/                 download the needs into the box                  <- same package
    __init__.py            fetch_all(), fetch_into_box(system, ...), failures()
    in_container.py  (new) fetch_os_packages(), run_in_container(): the shared `docker run` of apt/apk/rpm
    apt.py apk.py rpm.py   one kind each (distro tables in distros.py; rpm.REPOSITORIES stays: mirror keys)
    pypi.py npm.py images.py files.py
  hosts/                 what each server runs: inspect, cache, roles, drift   <- hosts.py
    __init__.py            overview, re-exports
    inspect.py             FACTS_SCRIPT, INSTALLED, inspect_server()
    cache.py               record_facts(), cached_facts(), inspect_and_record(), refresh_facts()
    roles.py               server_roles()
    drift.py               pinned_versions(), version_drift()
  egress.py              the seal: seal_script, UNIT, install_seal/seal_host/open_host/seal_status, target_hosts
                         <- infra/egress.py
  hostsfile.py           keep the Gitea name in /etc/hosts: boot_script, rewrite_script, keep_names  <- infra/hostsfile.py
  client_config/         files that point pip/npm/apt/apk/dnf/Docker at Gitea   <- infra/clients.py
    __init__.py            overview, re-exports
    paths.py               PATH_* constants, PRIVATE_NETS, RUNNER_HOME, STOCK_RPM_REPOS
    package_sources.py     apt_source_line, apk_repository_line, rpm_repo_section, client_files,
                           apt_trusts_the_ca, disabled_apt_sources, disabled_os_sources
    proxy_settings.py      proxy_env, proxy_exports, docker_client_config, environment_script, NO_PROXY_ENV
    node_files.py          corefile, profile_script, node_files            <- host_files
    config_image.py        config_image_files
  gitea/                 install Gitea, talk to it, fill its registries
    __init__.py            overview, re-exports (setup, upload, backup, restore, connect, ...)
    service.py             paths on the server, GITEA_CLI, wait_for_gitea, installed_version, token_works() (token on stdin)
    connect.py             connect(system), api_token(system); callers use `gitea.connect` through the package
    client.py              GiteaClient: version, orgs, repos, tokens, secrets, runners, workflow runs; .packages
    packages.py      (new) GiteaPackages, AlpineKey: upload_* per registry (files streamed), keys, has_*, push_image   <- client.py
    upload_formats.py (new) multipart, pypi_name_version, npm_package_json, upload_outcome, UPLOADED, EXISTS  <- client.py
    setup/                 `outpost gitea setup`                          <- gitea/setup.py
      __init__.py            setup(system, log): the seven `==> [n/7]` steps, _record_gitea
      files.py               APP_INI, UNIT, TOKEN_SCOPES
      server.py              connect_and_check_arch, ensure_certificates
      binary.py              check_box_has_gitea, install_binary (backup before upgrade), ensure_git
      service_config.py      make_git_user_and_folders, write_certificate, write_app_ini, start_gitea
      admin.py               ensure_admin_user, ensure_api_token, ensure_org
      seal.py                seal_gitea_server
    upload.py              upload(system, sources, log), upload_box, push_config_image, KIND_UPLOADERS, Tally
    backup.py              backup(system, out_dir=None, ...), restore(system, archive, log)
  infra/                 set up, verify, upgrade the nodes of an infrastructure
    __init__.py            overview, re-exports (set_up_infra, verify_infra, upgrade_nodes, TYPES, NodeContext, proxy)
    context.py       (new) NodeContext, node_context(system, infra), proxy_host_context(system)
                           <- infra/common.NodeContext, cli._node_context, the ctx in cli.cmd_proxy
    node/                  steps every node goes through                  <- infra/common.py
      __init__.py            prepare_node(host, ctx, log): six named steps
      names.py               write_hosts_entries
      trust.py               write_client_files, trust_ca, check_reaches_gitea
      os_packages.py         configure_os_packages, configure_apt_sources, configure_apk_sources, configure_rpm_sources
      proxy_settings.py      apply_proxy_settings, merge_proxy_settings
      seal.py          (new) seal_node (prepare_node and apply_proxy_settings)
      dns.py                 install_node_dns, DNS_UNIT
      docker.py              install_docker, DOCKER_UNIT, registry_verification
      download.py            download_from_gitea (was curl)
    compose.py swarm.py k3s.py   one infrastructure type each: setup(), verifications()
    runner.py              Actions runner: labels_for, install, register_script, install_runner_for(system, ...)
    verify.py              node_verifications, run_verifications, log_verifications, verification_script,
                           parse_verifications, by_family, gitea_only_sources_by_family
    setup.py         (new) set_up_infra(system, infra, log), verify_infra(system, infra, log), verifications_for,
                           TYPES  <- cli._infra_setup
    upgrade.py       (new) UPGRADE_SCRIPT, upgrade_nodes(system, hosts, log)  <- cli.UPGRADE_SCRIPT, _infra_upgrade
    proxy/                 the egress proxy (tinyproxy)                   <- infra/proxy.py
      __init__.py            overview, re-exports
      allow_list.py          allow_regex, allow_file, allowed_by, probe_name, refused_name
      tinyproxy.py           paths, LOG_PREP, LOGROTATE, conf, filter_type_line, unit, INSTALL
      setup.py               set_up_on_host(host, ctx, cfg, ...): six named steps
      diagnose.py            diagnose_script, access_script, access_report
      verify.py              host_verifications, verify_proxy_host, proxy_verifications (every node runs them too)
      recipe.py              set_up_egress_proxy(system, log), verify_egress_proxy(system), update_set_up_nodes(system, log)
                             <- cli.cmd_proxy
  checks/                smoke checks                                     <- checks/__init__.py
    __init__.py            overview, re-exports
    check.py               Check, REQUIREMENTS
    load.py                load_checks
    run.py                 checks_for(system, infra, only), run_checks(system, infra, checks, log), run_check, proxy_check_env(system)
                           <- cli.cmd_check, cli._proxy_check_env
  demo/                  the example project                              <- demo.py
    __init__.py            overview, re-exports
    workflow.py            FETCH_SOURCE, DEPLOY_STEPS, WAIT, PORTS, workflow()
    project.py             NAME, ensure_local(workspace), project_files, git_blob_sha, NEEDS_YML
    push.py                push(system, log), package_token(system, client)
    status.py              status(system), demo_urls(system)
  rotate_ca.py     (new) `pki rotate`: new CA, then Gitea, the config image and the nodes   <- cli.cmd_pki
  report/                the proof report                                 <- report.py
    __init__.py            overview, re-exports
    data.py                report_data(workspace, servers), servers_or_none(workspace)
    verdict.py             verdict()
    html.py                render_html(): one function per section
    style.py               CSS
    write.py               write_report(workspace, out)
  ui/                    the dashboard
    __init__.py            overview, re-exports serve
    server.py              Handler with route tables, serve()              <- ui/server.py (HTTP part)
    overview.py            overview(), needs_files, needs_detail, lab_status, ssh_aliases, list_backups
    files.py               editable_file (was safe_file), read_file, save_file, delete_needs_file, save_lockfile
    servers_form.py        write_servers, proxy_form
    reach.py               reach_server (was ssh_check), server_status, choose_ssh_config, refresh_hosts
    runs.py                ALLOWED_COMMANDS, start_run, run_status, past_run, live_runs
    status/                was ui/status.py
      __init__.py
      box_status.py          box_status, box_changes, spec_parts
      sync.py                sync
      preflight.py           preflight
      run_history.py         runs, steps
      search.py              search
    projects/              was ui/projects.py
      __init__.py
      lockfiles.py           describe_lockfile, lockfile name lists
      dockerfiles.py         read_dockerfile, needed_image, distro_for_image
      scan.py                scan, python_version
      write_needs.py         write_needs
    explain.py             FAILURES, explain, summarize (a table of per-command summarizers)
    index.html             same
  lab.py                 throwaway servers in Docker                       <- same, up() split
  templates/
    lab/Dockerfile         same
    workspace/             what `outpost init` copies                     <- templates/platform/
  cli/                   the command line (section 1.1)                    <- cli.py
  ssh/                   same, the model package
```

Gone: `paths.py`, `config.py`, `cli.py`, `oci.py`, `hosts.py`, `demo.py`, `report.py`,
`infra/common.py`, `infra/clients.py`, `infra/egress.py`, `infra/hostsfile.py`, `infra/proxy.py`,
`needs/parsers.py`, `ui/status.py`, `ui/projects.py`, `gitea/setup.py`.

### 1.1 `outpost/cli/`: the table of contents

```
cli/
  __init__.py     the flow of the whole tool in the docstring, in the order you use it; re-exports main, build_parser
  main.py         COMMANDS (ordered tuple of the modules below), build_parser(), main(): dispatch, errors -> exit codes
  output.py       say() (was log), say_error()
  init.py  needs.py  fetch.py  pack.py  gitea.py  upload.py  infra.py  check.py  demo.py
  egress.py  proxy.py  ui.py  lab.py  hosts.py  report.py  pki.py
```

Every command module has the same two functions, so `main.py` builds the parser by looping over
`COMMANDS`:

```python
"""outpost upload: push the box into Gitea's registries."""

def add_parser(commands) -> None:
    parser = commands.add_parser("upload", help="push the box into Gitea")
    parser.add_argument("source", nargs="*", help="box folder or tar.xz files (default: box/)")
    parser.set_defaults(run=run)

def run(args) -> int:
    system = AirGappedSystem.load(args.dir)
    gitea.upload(system, sources=[Path(s) for s in args.source], log=say)
    say("Next: outpost infra setup <name>")
    return 0
```

Each `run()` is a short recipe: load the system, call one or two module functions, print.
`pyproject.toml` keeps `outpost = "outpost.cli:main"`, so `cli/__init__.py` re-exports `main`.
The docstring of `cli/__init__.py` is also `outpost --help`, so its file map is a comment below it.

| today in cli.py | goes to |
|---|---|
| module docstring | `cli/__init__.py` docstring (also `--help`'s description) |
| `log` | `cli/output.say` |
| `_platform`, `_servers` | `AirGappedSystem.load` (`Workspace.open` for `ui`, `lab`, `report`) |
| `_ca_pem` | `AirGappedSystem.ca_pem()` |
| `_cached_facts` | `hosts.cached_facts(workspace).get(alias)`, used inside `endpoints.proxy_endpoint` |
| `_proxy_info` | `endpoints.proxy_endpoint(system, gitea=None)` |
| `_proxy_info_or_warn` | `endpoints.proxy_endpoint_or_none(system, gitea, log)` |
| `_add_proxy_package` | `needs.all_needs(system)` (loads needs, adds pinned files and tinyproxy) |
| `_node_context` | `infra.context.node_context(system, infra)` |
| `_print_results` | `infra/verify.log_verifications(node, results, log)` (built in A): infra setup/verify and the proxy verify log through it, so `rotate_ca` gets the same lines |
| `cmd_init` | `cli/init.py` + `Workspace.create(target)` |
| `cmd_needs` | `cli/needs.py` (prints) + `needs.all_needs(system)` |
| `cmd_fetch` | `cli/fetch.py` + `fetch.fetch_into_box(system, needs, only, log)` (writes state) + `fetch.failures(results)` |
| `cmd_pack` | `cli/pack.py` + `pki.ensure_box_signing_key(workspace)` + `box.pack` |
| `cmd_gitea` | `cli/gitea.py`: `setup`, `info`, `backup`, `restore` -> `gitea.setup/backup/restore(system, ...)` |
| `cmd_upload` | `cli/upload.py` + `gitea.upload(system, sources, log)` (open box, signature, endpoint, connect, upload_box, state) |
| `cmd_infra`, `_infra_setup` | `cli/infra.py` + `infra.set_up_infra` / `infra.verify_infra` |
| `UPGRADE_SCRIPT`, `_infra_upgrade` | `infra/upgrade.py: upgrade_nodes(system, hosts, log)` |
| `_install_runner` | `infra/runner.py: install_runner_for(system, ctx, host, log)` |
| `cmd_check`, `_proxy_check_env` | `cli/check.py` + `checks.checks_for(system, infra, only)` + `checks.run_checks(system, infra, checks, log)` + `checks.proxy_check_env(system)` |
| `cmd_demo` | `cli/demo.py` (prints the status table) + `demo.push/status(system)` |
| `cmd_proxy` | `cli/proxy.py` + `infra.proxy.set_up_egress_proxy(system, log)` / `verify_egress_proxy(system)` |
| `cmd_egress` | `cli/egress.py` + `egress.target_hosts(system, target)` + `egress.seal_host/open_host/seal_status` |
| `cmd_lab` | `cli/lab.py` + `lab.up/down(workspace)` |
| `cmd_ui` | `cli/ui.py` + `ui.serve(workspace, port)` |
| `cmd_hosts` | `cli/hosts.py` + `hosts.refresh_facts / cached_facts / version_drift` |
| `cmd_report` | `cli/report.py` + `report.write_report(workspace, out)` (loads servers.yml itself, tolerant) |
| `cmd_pki` | `cli/pki.py` + `rotate_ca.rotate(system, log)` |
| `build_parser` | `cli/main.py`, from each module's `add_parser` |
| `main` | `cli/main.py`; the "infra needs a name" check (exit 2) moves to `cli/infra.py` |

## 2. AirGappedSystem

It lives in `outpost/system.py`. It is a single module of about 100 lines that imports only
`workspace`, `config`, `ssh` and `versions`. It holds and reads things. It decides nothing.

```python
class AirGappedSystem:
    """Everything Outpost knows about one air-gapped system. It holds and reads; it has no logic."""

    workspace: Workspace     # the folder; every path comes from here
    servers: Servers         # servers.yml, read once by load()
    mirrors: Mirrors         # mirrors.yml (defaults when missing), read once by load()

    @classmethod
    def load(cls, root: str | Path) -> "AirGappedSystem": ...
```

| member | what it is | how it is loaded |
|---|---|---|
| `workspace` | `Workspace` | `Workspace.open(root)`: raises `ConfigError("No servers.yml in {root}. Run `outpost init` here, or pass --dir.")` (today's wording) |
| `servers` | `Servers` | `config.load_servers(workspace.servers_file)`, once, in `load()` |
| `mirrors` | `Mirrors` | `config.load_mirrors(workspace.mirrors_file)`, on first use (a `cached_property`), so a broken mirrors.yml stops only needs and fetch, as before |
| side effects of `load()` | | `ssh.use_config(servers.ssh_config)` and `versions.use_arch(servers.arch)`, as `cli._servers` does today |
| `state(name, default=None)` | one `state/<name>.json` | read from disk **on every call** (`workspace.read_state`) |
| `write_state(name, data)` | | `workspace.write_state` |
| `secret(name)` | one `secrets/<name>` | read from disk **on every call** |
| `write_secret(name, value)` | | `workspace.write_secret` (mode 600) |
| `ca_pem()` | text of `pki/ca.crt` | read on every call; raises `RuntimeError` with today's `_ca_pem` message when missing |
| `host(alias)` | `Host` | made on first use with the module-level name `Host`, then kept in a dict, so a test double sees every call. Tests patch `outpost.system.Host`. |
| `gitea_host` | `Host` | `host(servers.gitea.host)` |
| `nodes(infra_name)` | `list[Host]` | `servers.infra(name).nodes` (raises `ConfigError` for an unknown name) |
| `proxy_host` | `Host \| None` | `host(servers.egress_proxy.host)` when servers.yml has an egress proxy |
| `infra(name)` | `InfraConfig` | `servers.infra(name)` |
| `set_up_infras()` | `list[InfraConfig]` | the infrastructures that have a `state/infra-<name>.json` (used by proxy setup, pki rotate, demo push) |
| `gitea_is_set_up()` | `bool` | `state/gitea.json` exists (pki rotate) |
| `AirGappedSystem(workspace, servers)` | | the plain constructor; `load()` is the usual way in |

Things it does **not** hold: the Gitea and proxy endpoints (`endpoints.py` works them out, because
that takes rules and maybe an ssh call), needs (`needs.all_needs(system)`), and anything cached
from state.

`Workspace` (in `outpost/workspace.py`):

- Constants for each name in the layout: `SERVERS_FILE = "servers.yml"`, `MIRRORS_FILE`, `NEEDS_DIR`,
  `CHECKS_DIR`, `BOX_DIR`, `PKI_DIR`, `SECRETS_DIR`, `STATE_DIR`, `BACKUPS_DIR = "backups"`,
  `LAB_DIR`, `DEMO_DIR`, `HOSTS_STATE_DIR = "hosts"`, `RUNS_DIR = "runs"`, `REPORT_FILE`.
- Constants for the pki and secret files: `CA_CERT`, `CA_KEY`, `GITEA_CERT`, `GITEA_KEY`,
  `BOX_PUBLIC_KEY`, `BOX_SIGNING_KEY`, `GITEA_TOKEN`, `GITEA_ADMIN_PASSWORD`, `GITEA_PACKAGE_TOKEN`.
- Constants for the state files: `STATE_FETCH`, `STATE_UPLOAD`, `STATE_GITEA`, `STATE_PROXY`,
  `STATE_BACKUP`, `STATE_DEMO`, `STATE_DEMO_STATUS`, and the functions `infra_state(name)`,
  `verify_state(name)` and `check_state(name)`.
- A property for each folder and file, which joins the constant to `root`: `servers_file`,
  `box_dir`, `backups_dir`, `hosts_state_dir`, `runs_dir`, `ca_cert`, ...
- `exists()`, `Workspace.open(root)` (it must exist), `Workspace.create(target)` (copies
  `templates/workspace`, today's `cmd_init`), `read_state`/`write_state`,
  `read_secret`/`write_secret`, `state_names()` (for the report and the dashboard), and
  `state_mtime(name)`.

## 3. Renames

| today | new | note |
|---|---|---|
| `outpost/paths.py`, `Platform` | `outpost/workspace.py`, `Workspace` | |
| parameter `platform` (everywhere) | `system` (functions that read config or use servers), `workspace` (report, ui, lab, hosts cache, pki) | |
| `platform.box_dir` etc. | `workspace.box_dir` / `system.workspace.box_dir` | |
| `--dir` help "platform folder" | "workspace (default: current directory)" | help text only |
| `templates/platform/` | `templates/workspace/` | update `pyproject.toml` package-data too |
| `cli.py`, `cmd_<x>` | `cli/<x>.py`, `run(args)` | `args.func` -> `args.run` |
| `cli.log` | `cli.output.say` | |
| `config.py` | `config/` package | `config.__all__` no longer lists `versions` |
| `infra.clients.GiteaInfo` | `endpoints.GiteaEndpoint` | it says how nodes reach Gitea; easy to confuse with `GiteaConfig` today |
| `infra.clients.ProxyInfo` | `endpoints.ProxyEndpoint` | |
| `gitea.connect.gitea_info(platform, servers)` | `endpoints.gitea_endpoint(system)`, and the pure `endpoints.make_gitea_endpoint(gitea_config, gitea_state)` | done in A; `demo` calls the pure one until C gives it a `system` |
| `infra.proxy.info(cfg, gitea, gitea_host, ip)` | `endpoints.make_proxy_endpoint(...)` | the pure part |
| `gitea.connect.token_for(platform)` | `gitea.connect.api_token(system)` | |
| `gitea.connect.connect(platform, servers)` | `gitea.connect(system)` | |
| `gitea.setup.GITEA` | removed; use `service.GITEA_CLI` | it was an alias |
| `GiteaClient.upload_*`, `*_key`, `*_has_*`, `push_image`, `registry` | `GiteaPackages.*`, reached as `client.packages` | |
| `client._outcome` | `upload_formats.upload_outcome` | |
| `versions.platform_files` | `versions.pinned_files` | the pinned downloads (gitea, docker, k3s, ...) |
| `versions.names()` | `versions.arch_names()` | |
| `needs.add_platform_files` | `needs.add_pinned_files` | |
| `needs._parse_one` | `needs.read_needs_file` (public; the dashboard uses it) | |
| `needs.parsers` | `needs.python_lockfiles`, `needs.js_lockfiles` | `parse_python_file`, `parse_js_file` -> `read_python_lockfile`, `read_js_lockfile`; the `parse_<format>` functions keep their names |
| `infra.proxy.package_need`, `add_package` | `needs.proxy_package_need`, `needs.add_proxy_package` | |
| `infra.common.os_family`, `apk_branch_of` | `distros.os_family`, `distros.apk_branch_of` | |
| `fetch.rpm.split`, `distro_for_host`, `OS_IDS`; `fetch.apt.DEBIAN`, `UBUNTU` | `distros.split_rpm_distro`, `rpm_distro_of_host`, `RPM_OS_IDS`, `DEBIAN`, `UBUNTU` | `rpm_distro_of_host(facts)` takes the facts dict, as `os_family` does. `fetch.rpm.DISTROS` is split: images go to `distros.RPM_IMAGES`, mirror keys and paths stay as `fetch.rpm.REPOSITORIES` |
| `infra.proxy.PACKAGE` | `needs.PROXY_PACKAGE` | |
| `box` signature check in `gitea/upload._check_signature` | `box.check_signature(box, box_public_key)` | returns `SIGNATURE_VALID`, `SIGNATURE_INVALID`, `UNSIGNED` or `NO_PUBLIC_KEY` (not `bool \| None`: upload says different things for "unsigned" and "no box.pub") |
| `ui.projects.kind_for_distro` | `distros.kind_for_distro` | |
| `pki.ensure_ca(pki_dir, secrets_dir)` | `pki.ensure_ca(workspace)` | |
| `pki.ensure_server_cert` | `pki.ensure_gitea_cert(workspace, name, ip)` | |
| `pki.ensure_signing_key` | `pki.ensure_box_signing_key(workspace)` | |
| `pki.sign`, `pki.verify` | `pki.sign_file`, `pki.signature_is_valid` | |
| `infra/egress.py` | `outpost/egress.py` | it is used by gitea, infra, proxy, cli, ui |
| `egress.install`, `seal`, `open_`, `status`, `script` | `install_seal`, `seal_host`, `open_host`, `seal_status`, `seal_script` | |
| `egress.PRIVATE` | `client_config.PRIVATE_NETS` (one list) | it is a copy of the same list today. Since A, `clients.PRIVATE_NETS` lives in `endpoints.PRIVATE_NETS` (no_proxy needs it, and endpoints cannot import `infra`); D moves it to `client_config` and makes endpoints import it from there |
| `infra.common.PROXY_USER` | `egress.PROXY_USER` | the only owner the seal exempts; done in A (`infra/egress.py`), so `infra/context.py` need not import `common` |
| `infra.common.RUNNER_HOME` | `client_config.RUNNER_HOME` (runner.py uses it too) | today it is a copy of `runner.HOME` |
| `infra/hostsfile.py`: `script`, `rewrite`, `install` | `outpost/hostsfile.py`: `boot_script`, `rewrite_script`, `keep_names(host, entries)` | |
| `infra/clients.py` | `outpost/client_config/` | `host_files` -> `node_files` |
| `infra/common.py` | `infra/node/` | "common" says nothing |
| `common.curl(ctx, url, out)` | `node.download_from_gitea(url, out)` | `ctx` was never used |
| `common._configure_apt/_apk/_rpm` | `node.configure_apt_sources/...` (public) | the proxy setup calls them |
| `common.install_dns` | `node.install_node_dns` | |
| `common.registry_check` | `node.registry_verification` | glossary: one line of verify is a verification |
| `verify.common_checks`, `proxy_checks`, `script`, `parse` | `node_verifications`, `proxy.proxy_verifications`, `verification_script`, `parse_verifications` | the `CHECK\|` marker on the wire stays (tests match it). `proxy_verifications` lives in `infra/proxy/verify.py`, and `infra/setup.verifications_for` adds it to the list, so `verify` and `proxy` no longer import each other |
| `verify.verify_node(host, ctx, extra)` | `verify.run_verifications(host, verifications)` | the caller passes the whole list (`verifications_for`) |
| `verify.source_commands` | `verify.gitea_only_sources_by_family` | |
| the arch check, copied in `prepare_node`, `gitea/setup` and `proxy.setup` | `versions.check_server_arch(alias, facts)` | not `infra/node/checks.py`: gitea (layer 2) uses it too, and "check" means a smoke check |
| `<type>.extra_checks(ctx)`, `runner.extra_checks()` | `<type>.verifications(ctx)`, `runner.verifications()` | the verification names (strings) stay |
| `proxy.host_checks`, `proxy.verify` | `proxy.host_verifications`, `proxy.verify_proxy_host` | |
| `hosts.inspect`, `record`, `cached`, `refresh`, `roles`, `drift`, `expected_versions`, `hosts_dir` | `inspect_server`, `record_facts`, `cached_facts`, `refresh_facts`, `server_roles`, `version_drift`, `pinned_versions`, `workspace.hosts_state_dir` | |
| `report.build`, `render`, `write` | `report_data`, `render_html`, `write_report` | |
| `ui.server.ssh_check` | `ui.reach.reach_server` | glossary: "check" means a smoke check; the URL `/api/ssh-check` stays |
| `ui.server.safe_file` | `ui.files.editable_file` | |
| `demo.urls` | `demo.demo_urls` | |
| `demo._blob_sha` | `demo.git_blob_sha` | |

## 4. Work packages

Run them **in order, A then B then C then D**. Each one must leave everything green on its own
(`CLAUDE.md`: format, ruff, pyright, pytest). D is the largest. If it gets too big, split it into
D1 (infra, egress, hostsfile, client_config) and D2 (hosts, report, ui, lab).

**Before A,** save the help output so you can compare it at the end of each step:
`(python -m outpost --help; for c in init needs fetch pack gitea upload infra check demo egress proxy ui lab hosts report pki; do python -m outpost $c --help; done) > help-before.txt` (outside the repo).
After each step, the diff may only show "platform folder" changing to "workspace".

**Every step checks the integration tests too.** They need Docker to run. Without
`OUTPOST_INTEGRATION=1` they are skipped, but their module-level imports still run. So run
`python -m pytest -q tests/integration` and `grep -n "from outpost" tests/integration/*.py` (some
imports sit inside functions). Fix every old name you find.

### A. Foundation: workspace, config, system, endpoints, cli/

| | |
|---|---|
| **New / moved** | `workspace.py` (from `paths.py`), `config/` package, `system.py`, `endpoints.py`, `cli/` package; `templates/platform` -> `templates/workspace` (+ `pyproject.toml` package-data) |
| **Touched** | every module that imports `paths`/`Platform` or `config` (all of them); every test that uses `Platform` (about 120 places) or `outpost.cli` internals; `__main__.py` |
| **Main splits** | `cli.py` into 16 command files and `main.py`/`output.py`. The cli helpers move to their final homes and **take `system` from the start**: `endpoints.gitea_endpoint/proxy_endpoint/proxy_endpoint_or_none`, `infra/context.node_context` (with `NodeContext` moved there), `infra/setup.set_up_infra/verify_infra`, `infra/upgrade.upgrade_nodes` (with `UPGRADE_SCRIPT`), `infra/runner.install_runner_for`, `checks.run_checks`, `needs.all_needs` (in `needs/__init__.py` for now; B moves it), the `cmd_proxy` body as `set_up_egress_proxy`/`verify_egress_proxy` at the bottom of `infra/proxy.py` (D moves it), `gitea/upload.upload(system, ...)`, `rotate_ca.rotate(system, log)`. `load_servers` becomes `_load_gitea`, `_load_infrastructures`, `_load_arch` and `load_egress_proxy`. |
| **Signatures** | **Decision:** A renames `Platform` to `Workspace` everywhere (a mechanical change). Functions that already exist keep their parameters, so the cli passes `system.workspace, system.servers` to them for now. Each later package converts its own functions to `system` and updates the cli lines that call them. A stays reviewable, and B, C and D each own their own signature changes. |
| **Risks** | Tests patch `outpost.cli.Host` (test_proxy: 668, 689, 802): change them to `outpost.system.Host`. That only works if every Host those commands use comes from `system`, so `node_context`, `run_checks` and the proxy recipe must use `system.host()`. Tests patch `cli.log` and call `cli._infra_upgrade(platform, servers, infra, [host])` (test_day_two): change them to `upgrade_nodes(system, hosts, log=...)`. `test_proxy` checks `args.func is cmd_proxy`: change it to `args.run is proxy_command.run`. Error output: `main` must still print `error: <message>` to stderr with exit 1 for `ConfigError, RuntimeError, SshError, HttpError, ValueError`, exit 2 for a missing infra name, and 130 on Ctrl-C. Tests read stderr ("no egress_proxy section"). `AirGappedSystem.load` reads mirrors.yml for every command, where today only needs and fetch do. Grep the tests for a broken mirrors.yml used with any other command. Import cycles: `infra/setup.py` imports `gitea.connect` while `gitea/setup.py` still imports `infra.egress`. Keep `infra/__init__.py` from importing `infra/setup.py` until D moves egress down a layer. The dashboard runs `python -m outpost --dir <root> ...`: that keeps working. Tests build paths to `templates/platform` (test_checks: 7, test_proxy: 818). |
| **Verify** | the four checks in CLAUDE.md; the help diff; `python -m pytest -q tests/integration` (imports); `grep -rn "Platform\|outpost.paths\|platform_dir\b" outpost` is empty (the test fixture `platform_dir` may become `workspace_dir` in tests); `python -m outpost init /tmp/w && python -m outpost --dir /tmp/w needs` works |
| **Done (as built)** | All of the above. Also in A: `fetch.fetch_into_box` and `fetch.failures` (listed under B); `egress.target_hosts(system, target)` in `infra/egress.py`; `hosts.refresh` gets `make_host=system.host`; bug 1 on the cli side (`gitea backup --out` defaults to `workspace.backups_dir`); `upload` prints a `warning:` line when the box is unsigned or `pki/box.pub` is missing. `set_up_infra`, `verify_infra`, `upgrade_nodes` and `rotate` return a bool (all passed); the cli turns it into the exit code and prints "Next: ..." itself, so `pki rotate` no longer prints that line per infrastructure. Left for later: `needs.all_needs` imports `infra.proxy` inside the function until B moves the proxy package rules and the distro tables down; `infra/proxy.verify` and `infra/verify.proxy_checks` still import each other lazily (D); `gitea.setup`, `gitea.backup`, `demo` still make their own `Host` and take `(workspace, servers)` (C). |

### B. Getting things in: needs, http, oci, box, fetch, versions, pki, distros

| | |
|---|---|
| **Files** | `needs/` (model, needs_file, pinned, python_lockfiles, js_lockfiles), `distros.py` (new), `versions.py`, `http.py`, `oci/` (refs, registry, layout), `box.py`, `fetch/` (+ `in_container.py`), `pki.py`; the callers in `infra/common.py`, `infra/proxy.py`, `ui/projects.py`, `ui/status.py`, `ui/server.py`, `gitea/*`, `cli/needs.py`, `cli/fetch.py`, `cli/pack.py` |
| **Main splits** | `needs._parse_one` (noqa C901) becomes `_read_images`, `_read_pypi`, `_read_npm`, `_read_os_packages`, `_read_files`. `versions.platform_files` (72 lines) becomes `_gitea_files`, `_node_files`, `_docker_files`, `_k3s_files`. `oci.Registry.pull` (57 lines) becomes `_get_manifest`, `_pick_arch`, `_fetch_blobs`, `write_index`, and `build_layout` shares `write_index`. apt/apk/rpm `fetch` share `run_in_container(image, shell, script, out_dir)`. `cli.cmd_fetch`'s state write and failure list become `fetch_into_box(system, needs, only, log)` and `failures(results)`. The signature check from `cmd_upload` becomes `box.check_signature(box_dir, box_pub) -> bool \| None`. `pki` functions take a `workspace`. `needs/parsers.py` imports `NpmNeed` lazily to dodge a cycle; `needs/model.py` removes the cycle. |
| **Also** | `http` logs every request and download at DEBUG (style guide). Keep the retry line on stderr: tests and users see it. |
| **Risks** | `test_fetch` patches `mod.fetch` for each kind in `fetch_all`'s plan: keep the plan's call shape `fetch(needs, box, mirrors, log=...)`. `versions.ARCH` stays a module global that `use_arch` sets: oci, fetch and infra read it at call time, so don't import the name `ARCH` by value. `test_infra` and `test_proxy` call `os_family`, `package_need` and `add_package` under their old names. The pip download writes `box/pypi/.requirements-py*.txt`: `upload` and `box_status` filter by suffix, so keep that. `test_real_gitea` imports `oci` and `fetch.apk`. |
| **Verify** | CLAUDE.md checks; help diff; `outpost needs` output for the template workspace is identical before and after (save it in A) |
| **Done (as built)** | All of the above, plus: `distros.distro_of_host(facts)` (the proxy setup's `_distro_of` uses it); `fetch.in_container.fetch_os_packages(kind, packages, box, OsPackageRecipe, log, run)` holds the per-distro loop that apt, apk and rpm shared, and `run_in_container(image, shell, script, out_dir, label, run)` the `docker run`; `oci/media_types.py` holds the media types (so `layout` and `registry` need not import each other); Workspace gets `ca_key`, `gitea_cert`, `gitea_key`, `box_signing_key`. `http` retries only GET and HEAD (a PUT or POST that got a 5xx may have landed); `Http.request(data=Path)` streams the file, and `oci.Registry` pushes blobs that way. Bugs fixed: an unquoted `python: 3.10` in a needs file is an error that says to quote it (YAML reads it as 3.1); Gitea's certificate is reissued when its names differ (the check was a substring match on the name and ignored the IP); `write_manifest` drops a `manifest.sig` left by an earlier pack (fetch after pack made upload refuse box/); one image or file that fails no longer stops the rest of its kind (they list it under `failed`, as npm and pypi do); a 202 without `Location` on a blob upload is an `HttpError`. Left for later: `gitea/client.py` uploads still read whole files into memory (C: `Http.request` takes a `Path` now); `Registry.push` still returns only the digest (bug 7, C); `ui/projects.py` keeps its image-name heuristics (`_CODENAMES`, `_UBUNTU`, `_RPM_IMAGES`, D); `hosts.cached` is still called under its old name from `needs/pinned.py` and `ui/server.py` (D). |

### C. Gitea, checks, demo

| | |
|---|---|
| **Files** | `gitea/` (service, connect, client, packages, upload_formats, setup/, upload, backup), `checks/` package, `demo/` package, `cli/gitea.py`, `cli/upload.py`, `cli/check.py`, `cli/demo.py`, `rotate_ca.py`, `ui/server.py` (demo calls) |
| **Signatures** | `gitea.setup(system, log)`, `backup(system, out_dir=None, keep_on_server=False, log)`, `restore(system, archive, log)`, `connect(system)`, `demo.push(system, log)`, `demo.status(system)`, `demo_urls(system)`, `run_checks(system, ...)`. Hosts come from `system.gitea_host`. Drop the `host=` test hook from backup/restore: tests patch `outpost.system.Host`. |
| **Main splits** | `gitea.setup` (noqa C901, 140 lines) becomes `setup/`: `check_box_has_gitea`, `connect_and_check_arch`, `ensure_certificates`, `install_binary` (with the backup first), `ensure_git`, `write_app_ini`, `start_gitea`, `ensure_admin_user`, `ensure_api_token`, `ensure_org`, `seal_gitea_server`. `upload_box` (noqa C901) becomes a `KIND_UPLOADERS = {kind: fn}` table plus `_upload_<kind>(box, packages, tally)`, with a small `Tally` class. `restore` (55 lines) becomes `_copy_archive_up`, `_swap_data`, `_put_back_laptop_certificate`, `_check_token`, `_record_restore`. The "does this token work" curl, duplicated in setup and restore, becomes `service.token_works(host, token)`. `client.py` (315 lines, two ideas) becomes `client.py` + `packages.py` + `upload_formats.py`. `checks/__init__.py` becomes check/load/run. `demo.py` becomes workflow/project/push/status. |
| **Bug fixes here** | the backup folder (bug 1), the images tally (bug 7), the backup sha256 (bug 8) |
| **Risks** | `test_gitea_setup` and `test_day_two` patch `setupmod.Host` and `setupmod.connect`: change them to `outpost.system.Host` and to patching `outpost.gitea.setup.<step module>.connect`, or pass a client. `test_demo` patches `demo.connect` with `lambda p, s: Conn()`: it now takes `system`. `test_proxy` patches `outpost.checks.run_check`, but `run_checks` calls it from inside `checks/run.py`, so tests must patch `outpost.checks.run.run_check`. `test_gitea_client` and `test_real_gitea` call `client.upload_*`: change them to `client.packages.upload_*`. The integration flow reads `rig/backups/` (test_full_flow: 120, 162): after the fix, the archive lands there. Keep the `==> [n/7]` step lines: the dashboard's progress panel and `explain.summarize` parse them ("Gitea X is up at", "backup done: ... (N MB)", "N uploaded, M already there", "committed N file"). |
| **Verify** | CLAUDE.md checks; help diff; `grep -rn "noqa: C901" outpost/gitea` is empty |
| **Done (as built)** | All of the above. `gitea/__init__.py` re-exports `setup`, `upload`, `upload_box`, `push_config_image`, `backup`, `restore`, `connect`, `api_token`, `GiteaClient`, `GiteaPackages`, `AlpineKey`. Every caller (cli, `rotate_ca`, `infra/runner`, `demo`, the setup's `ensure_org`) calls `gitea.connect(system)` through the package, so tests fake the API by replacing one name, `outpost.gitea.connect`, as they replace `outpost.system.Host` for ssh. The setup's steps live in `setup/server.py` (new: `connect_and_check_arch`, `ensure_certificates`), `binary.py`, `service_config.py` (also `make_git_user_and_folders`, `write_certificate`), `admin.py` (also `ensure_org`) and `seal.py`; the `==> [n/7]` lines are unchanged. `oci.Registry.push` returns `oci.Pushed(digest, uploaded)`: uploaded is False when every blob was there and the tag already named the manifest (then no manifest PUT either). The ui builds an `AirGappedSystem` for `demo.demo_urls` and `demo.status`. Bugs fixed: images counted as uploaded when Gitea had them (bug 7); the backup's sha256 read the whole archive and was skipped above 2 GB (bug 8, now `sha256_file`); generic, Debian, Alpine and RPM uploads read the whole file into memory (now streamed); the API token was on curl's command line on the Gitea server in setup and restore (now `service.token_works`, header on stdin); `docs/architecture/gitea.md` said the token's scope was `all`. Left for later: the admin password is still on the `gitea admin user create` command line during the first setup; `gitea/setup/seal.py` and `gitea/upload.py` still import `infra.egress`, `infra.hostsfile`, `infra.clients` (D moves them down); `hosts.record`/`hosts.inspect` keep their old names (D); the runner's registration token is on `gitea-runner register`'s command line (`infra/runner.py`, D). |

### D. Infrastructure, servers, report, dashboard, lab

| | |
|---|---|
| **Files** | `egress.py`, `hostsfile.py`, `client_config/` (moved down a layer), `infra/` (context, node/, compose, swarm, k3s, runner, verify, setup, upgrade, proxy/), `hosts/`, `report/`, `ui/` split, `lab.py`, `cli/infra.py`, `cli/proxy.py`, `cli/egress.py`, `cli/hosts.py`, `cli/report.py`, `cli/ui.py`, `cli/lab.py`, `cli/pki.py` |
| **Main splits** | `prepare_node` (55 lines) becomes `check_arch`, `write_hosts_entries`, `write_client_files`, `trust_ca`, `check_reaches_gitea`, `configure_os_packages`, `seal`, `install_node_dns`. `check_arch` is shared with gitea setup and the proxy setup, which copy it today. `proxy.setup` (110 lines) becomes six step functions (`connect_and_check_host`, `point_packages_at_gitea`, `install_tinyproxy`, `write_user_and_config`, `start_service`, `seal_with_exception`). `cmd_proxy`'s body becomes `proxy/recipe.py`. `report.render` (noqa C901) becomes one function per section. `ui.Handler.do_GET` (noqa C901) becomes route tables `{path: handler}` for GET/PUT/POST/DELETE. `status.box_status` and `box_changes` (noqa C901) become one function per kind. `explain.summarize` (noqa C901) becomes `SUMMARIZERS = {command: fn}`. `lab.up` (70 lines) becomes `_build_image`, `_start_servers`, `_write_lab_config`, `_forget_old_servers`, `_wait_for_ssh`. `hosts.py` becomes inspect/cache/roles/drift. |
| **Signatures** | `hosts.refresh_facts(system, aliases, log)`, `inspect_and_record(system, host)`; `report.*(workspace, ...)`; `ui.serve(workspace, port)`; `lab.up(workspace, k3s, log)`; egress and verify functions keep `host`/`ctx`. |
| **Bug fixes here** | bugs 2 to 6 and 9 |
| **Risks** | `test_proxy` patches `common.prepare_node` (415, 449), which becomes `outpost.infra.node.prepare_node`: the proxy setup must call it through the name the test patches. Verification names and the `CHECK\|` marker are read by the tests, the report and the integration flow ("tests: full flow reads check results by name"), so keep every name string. The seal script text is checked by a verification (`grep -q 'build_chain $ipt security DROP'`): keep it byte for byte. Moving egress, hostsfile and client_config out of `infra/` breaks `test_real_gitea`'s `from outpost.infra import clients` and `from outpost.infra.clients import GiteaInfo`: update them. The dashboard's JSON keys (`/api/overview`) and URLs are read by `ui/index.html`: keep them. `test_full_flow` (387 to 393) calls `ui.server.overview(Platform(rig))` and `refresh_hosts`: point it to the new names. |
| **Verify** | CLAUDE.md checks; help diff; `grep -rn "noqa: C901" outpost` is empty; `outpost ui` opens and every page loads against the template workspace; when Docker is around, `OUTPOST_INTEGRATION=1 python -m pytest -q tests/integration` |
| **Done in D1 (as built)** | D was split. D1 did `egress.py`, `hostsfile.py`, `client_config/`, `infra/` (context, node/, compose, swarm, k3s, runner, verify, setup, upgrade, proxy/) and their callers in `cli/`, `gitea/`, `ui/server.py`, `rotate_ca`. gitea no longer imports infra (a test checks it). Differences from the tree above: `node/checks.py` became `versions.check_server_arch`; `node/shell.py` is `node/download.py`; `node/seal.py` is new; `TYPES` lives in `infra/setup.py` (re-exported) so `infra/__init__.py` can import setup; `proxy_verifications` is in `infra/proxy/verify.py`. `client_config` imports the endpoint types under `TYPE_CHECKING` only, because `endpoints` imports `PRIVATE_NETS` from it. What lands on servers was compared byte for byte, old against new, for every script and file of every step (FakeHost, all families, with and without a proxy): the same, except the bug fixes below and one doubled space in the k3s install line. Bugs fixed: 2 (`${ID:-} ${ID_LIKE:-}`), 3 (`shlex.quote`, also for K3S_URL and K3S_TOKEN), the runner's registration token is in `GITEA_RUNNER_REGISTRATION_TOKEN` instead of `--token` (gitea-runner 5.2.2 reads it; the script goes on stdin). Also found and fixed: `egress.seal_host`/`open_host` put `--reason` into the command unquoted, so the dashboard's free-text reason with a `'` failed and `$(...)` in it ran on the server; now `shlex.quote` (the egress log line reads the same). Runner jobs still run as root: a runner user would still need Docker or the k3s admin config, which are root-equivalent, so it is a documented limit (`docs/reference/limits.md`). Left for D2: `hosts.record`, `hosts.inspect` and `hosts.cached` keep their names (callers in `infra/setup.py`, `infra/upgrade.py`, `infra/proxy/recipe.py`, `gitea/setup`, `needs/pinned.py`, `endpoints.py`, `ui`, `report`); `hosts.SCRIPT` reads `$PRETTY_NAME` without `:-` under `set -u` (PRETTY_NAME is optional in os-release); `ui/server.server_status` still makes its own `Host`. |
| **Done in D2 (as built)** | `hosts/` (inspect, cache, roles, drift), `report/` (data, verdict, html, style, write), `ui/` split into server, overview, files, servers_form, reach, runs, `status/` (box_status, sync, preflight, run_history, search), `projects/` (lockfiles, dockerfiles, scan, write_needs) and explain, and `lab.up` in named steps. No `# noqa: C901` is left, in `outpost/` or `tests/` (the fake Gitea in `tests/conftest.py` answers from route tables too). Renames: `hosts.inspect/record/cached/refresh/roles/drift/expected_versions/SCRIPT` -> `inspect_server`, `record_facts`, `cached_facts`, `refresh_facts(system, aliases, log, make_host=None)`, `server_roles`, `version_drift`, `pinned_versions`, `FACTS_SCRIPT`; the setup and verify commands call `inspect_and_record(system, host)`. `report.build/render/write` -> `report_data`, `render_html`, `write_report(workspace, out=None)` (reads servers.yml itself through `servers_or_none`); the page is byte for byte the same except the heading "What this workspace is". `ui.Handler` answers from `GET_ROUTES`, `GET_PREFIX_ROUTES`, `PUT_ROUTES`, `POST_ROUTES`, `DELETE_ROUTES`; the API paths and JSON are unchanged (`/api/overview` was compared old against new on a filled workspace). `ui/__init__.py` re-exports `serve`, `Handler`, `overview`, `refresh_hosts`, `reach_server`, `server_status`, `ALLOWED_COMMANDS`. `explain.summarize` is a `SUMMARIZERS` table (compared old against new for every command). `needs` gets `needs_and_pinned_files(needs_dir, mirrors, infra_types)` and `all_needs(system, skip_unknown_proxy_distro=False)`; the dashboard uses them instead of its own copy, so its box status now also lists tinyproxy when the proxy host's distro is known. `projects/dockerfiles` takes the rpm image names from `distros.RPM_IMAGES`. "this platform is built for" is "this workspace is built for", and index.html says workspace. Bugs fixed: 4, 5, 6, 9; `FACTS_SCRIPT` read `$PRETTY_NAME` and `$ID` without `:-` and stopped at a missing os-release under `set -eu`; the dashboard took POSTs from any web page (no Origin check, text/plain JSON accepted): now a non-GET request with a foreign Origin, a POST or JSON PUT without `Content-Type: application/json`, and any request whose Host is not 127.0.0.1 or localhost (DNS rebinding) get 403, and index.html sends the header; the flaky `coredns at pinned version` after a reboot was `coredns -version \| grep -q`: grep exits at the match, coredns dies of SIGPIPE writing its second line, and `pipefail` failed the verification. Every version and source verification now uses `verify.output_contains(command, text)` (`grep -qF <<<"$(command)"`); the names are unchanged. The reboot test also waits until outpost-egress, outpost-dns and docker or k3s are all active. Deviations: `python_version` is in `projects/scan.py` (it reads the folder, not a Dockerfile); `ui/files.py` also has `read_file`, `save_file`, `delete_needs_file`, and `ui/runs.py` `past_run` (GET /api/runs/<id>). |

## 5. Known bugs to fix along the way

Line numbers are for today's code (commit `aac067b`).

| # | where | bug | fix in |
|---|---|---|---|
| 1 (fixed in A) | `cli.py:727`, `cli.py:252` | `gitea backup --out` defaults to `"backups"`, relative to the **current directory**, not the workspace. `gitea setup` (`gitea/setup.py:151`) and the dashboard (`ui/server.py:221`) use `<workspace>/backups`. So backups land wherever outpost is run, and `tests/integration/test_full_flow.py:161-162` (`test_2b`) restores the newest archive in `rig/backups`, which is the older one from the upgrade in `test_2`. Fix: default `None` -> `system.workspace.backups_dir`; help "(default: backups/ in the workspace)". | C |
| 2 (fixed in D1) | `infra/verify.py:21`, `cli.py:357` (`UPGRADE_SCRIPT`) | `case " $ID $ID_LIKE "` under `set -u`: `Host.run` puts `set -euo pipefail` at the top (`ssh/host.py:68`), and Debian and Alpine have no `ID_LIKE` in `/etc/os-release`. So on Debian and Alpine nodes, the verifications "package sources are gitea only" and "package index updates offline" fail ("ID_LIKE: unbound variable"), so does the proxy's "tinyproxy installed from gitea" (`infra/proxy.py:495`), and `infra upgrade` stops. The lab is Ubuntu, so the integration tests never see it. Fix: `${ID:-} ${ID_LIKE:-}` in both, plus a unit test whose script runs under `bash -u` with `ID_LIKE` unset. | D |
| 3 (fixed in D1) | `infra/k3s.py:75` | `INSTALL_K3S_EXEC={exec_args!r}` uses Python's `repr()` as shell quoting. An `args:` with a `'` makes repr use double quotes, and then the shell expands `$` and backticks inside them. Fix: `shlex.quote`. | D |
| 4 (fixed in D2) | `lab.py:125-130` | `lab up` forgets the old state (`state/*.json`, token, password) but keeps `state/hosts/*.json`. The dashboard's Machines page and the report then show the removed servers' versions and "sealed" for brand-new open ones until `hosts --refresh`. Fix: also clear `workspace.hosts_state_dir`. | D |
| 5 (fixed in D2) | `ui/server.py:173` | `load_mirrors` runs outside the `try` that guards the same call at line 167 (through `needs_detail`). A broken `mirrors.yml` turns all of `/api/overview` into a 400, so the dashboard shows nothing instead of the error. Fix: read mirrors once, inside the guarded block. | D |
| 6 (fixed in D2) | `ui/status.py:254-256` | One needs image that does not parse empties `wanted_images`, so every image in the box is listed as unneeded. Fix: parse each reference on its own and skip the bad ones. | D |
| 7 | `gitea/upload.py:84` | Images are always counted as "uploaded", even when Gitea already had every blob. The upload summary, the dashboard's history line ("Uploaded N new files") and the report's totals overcount. Fix: `push` returns whether anything was new. | C |
| 8 | `gitea/backup.py:57` | `hashlib.sha256(local.read_bytes())` reads the whole archive into memory (up to 2 GB) and skips the hash above that. Fix: `http.sha256_file(local)`, always. | C |
| 9 (fixed in D2) | `ui/status.py:274-282` | Dead code: the pypi loop in `box_changes` ends in `pass` and never adds anything. Remove it (the comment says why it must not flag dependencies). | D |

Worth a decision, but not bugs:

- Done in A: `upload` prints `warning: the box is not signed (no manifest.sig), ...` or
  `warning: cannot check the box's signature: pki/box.pub is missing` instead of skipping the check
  without a word. B's `box.check_signature` can take over the check; keep the two lines.
- `gitea/setup.py:216` and `gitea/backup.py:107`: the API token goes into a `curl` command line
  on the Gitea server, where `ps` can see it. `service.token_works` (package C) can pass it on
  stdin (`curl -H @-`).
- `pack --out dist` is relative to the current directory too. That looks deliberate, since the
  output is for releases, but say so in the help.
- `versions.ARCH` and `ssh.use_config` are process-wide globals that `AirGappedSystem.load` sets.
  That is fine for the CLI. A later step can turn them into attributes of the system.
