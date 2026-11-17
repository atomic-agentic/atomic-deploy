# atomic on one host, with Docker Compose

atomic's runtime on Postgres, with the observability stack from
[`../observability`](../observability): an OpenTelemetry Collector,
Prometheus, Tempo, Loki and Grafana with atomic's dashboards.

```text
                ┌────────────── atomic (compose project) ──────────────┐
  your project ─┼▶ atomic ──▶ postgres ◀── grafana (read-only login)   │
  (mounted)     │     │                       ▲                        │
  model server ◀┼─────┤ OTLP                  │                        │
  (on the host) │     ▼                       │                        │
                │  otel-collector ──▶ prometheus, tempo, loki ─────────┘
                └───────────────────────────────────────────────────────┘
```

| Service | Image | Host port (127.0.0.1) |
| --- | --- | --- |
| `atomic` | built here: an atomic release archive, plus the project's toolchains | none yet |
| `postgres` | `postgres:18.7-trixie` | 5432 |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.164.0` | 4317 (gRPC), 4318 (HTTP) |
| `prometheus` | `prom/prometheus:v3.5.5` | 9090 |
| `tempo` | `grafana/tempo:3.1.1` | none |
| `loki` | `grafana/loki:3.7.9` | none |
| `grafana` | `grafana/grafana:12.4.15` | 3000 |

> **Status.** atomic is in early development. Its binary does not have
> `atomic serve` yet, does not yet store workflows in Postgres, and exports
> little telemetry, so the `atomic` service exits until it does. Everything
> else runs today, and the dashboards can be checked with sample telemetry
> (see [Check it](#check-it)).

## Start

1. **Stage an atomic release.** The image is built from a release archive and
   its checksum, not from source:

   ```sh
   ./atomic/stage.sh                        # from ../../atomic/target/dist (a sibling checkout)
   ./atomic/stage.sh --checkout ~/src/atomic
   ./atomic/stage.sh --archive atomic-0.1.0-x86_64-unknown-linux-gnu.tar.gz
   ```

   In an atomic checkout, `scripts/release-package.sh` writes the archive to
   `target/dist/` after a release build. The image build checks the archive
   against its `.sha256`, which shows it arrived intact but not who made it:
   verify a downloaded release's signature before staging it.

2. **Configure.** Copy `.env.example` to `.env` and set every password, the
   model endpoint, and the project to run:

   ```sh
   cp .env.example .env
   openssl rand -hex 24   # once per password
   ```

   Use hex passwords: `ATOMIC_DB_PASSWORD` goes into a database URL as is,
   and the `/` and `+` that base64 produces break it. Passwords take effect
   when the Postgres volume is first created; changing them in `.env` later
   changes neither the database roles nor Grafana's admin.

   A model server on the Docker host is reachable from the container as
   `http://host.docker.internal:<port>`. `ATOMIC_PROJECT_DIR` is the directory
   holding your `atomic.yaml`; it defaults to the example Python project in
   `./project`. It is mounted at `/workspace/<ATOMIC_PROJECT_NAME>`. atomic
   keys its state by that path, so give each project its own name.

   The atomic image is built for that project; see
   [The project image](#the-project-image). The default expects a uv project.
   For any other, set `ATOMIC_PROJECT_IMAGE_DIR` to `../docker/base` or
   to an image of your own.

3. **Run.**

   ```sh
   docker compose up -d --build
   docker compose ps
   ```

   The atomic image is rebuilt on every `up` (`pull_policy: build`), so a
   newly staged release, a changed project image or a changed `uv.lock` is
   always used; unchanged layers come from the cache.

   Grafana is at <http://127.0.0.1:3000>. Its home page is the atomic
   Overview; the other dashboards are under **atomic dashboards**.

To run the stack without atomic, for example while developing atomic on the
host and exporting to the collector on 127.0.0.1:4317:

```sh
docker compose up -d postgres otel-collector prometheus tempo loki grafana
```

## The project image

The `atomic` service runs a **project image**: atomic's runtime image plus the
toolchains and packages the project's atom commands need, because a turn has
no network to download them. The images and what makes one are described in
[`../docker`](../docker).

Compose builds the runtime image from the staged release as the build-only
`atomic-runtime` service, then builds the directory in
`ATOMIC_PROJECT_IMAGE_DIR` on top of it, with `ATOMIC_PROJECT_DIR` as the
`project` build context. The default is
[`../docker/python`](../docker/python), which the
example project here is set up for. With it, a newly staged release or a
changed `uv.lock` rebuilds the image on the next `up`, and the container is
recreated from it, which also resets uv's cache.

## Check it

```sh
# from the repository root
python3 -I observability/tools/send-sample-telemetry.py --endpoint http://127.0.0.1:4318 --minutes 3
python3 -I observability/tools/check-dashboards.py --url http://127.0.0.1:3000 --password '<GRAFANA_ADMIN_PASSWORD from .env>'
```

Run any atomic command in the image, for example a single Codex turn against
the configured model:

```sh
docker compose run --rm atomic debug probe "Reply with one word: ready"
```

A one-off `run` shares the service's state volume and executor identity. That
is harmless for debug commands, but stop the `atomic` service first, or pass
`-e DBOS__VMID=<another id>`, before running anything that executes workflows:
two processes with one executor identity can each take the other's runs.

## What is where

| Path | Purpose |
| --- | --- |
| [`compose.yaml`](compose.yaml) | The services |
| [`.env.example`](.env.example) | Every setting, with its default |
| [`atomic/Dockerfile`](atomic/Dockerfile) | The runtime image: Debian slim, the binary, git, bubblewrap and tini, as a non-root user |
| [`atomic/stage.sh`](atomic/stage.sh) | Puts a release archive and its checksum in `atomic/dist/` |
| [`../docker/`](../docker) | Project images built on the runtime image, shared with other deployments: `python` (the default) and `base` |
| [`postgres/init/10-roles.sh`](postgres/init/10-roles.sh) | First start only: the `atomic` database and three logins |
| [`collector/postgres.yaml`](collector/postgres.yaml) | Merged over the shared collector config: Postgres metrics |
| [`project/`](project) | An example atomic project: a small Python library managed with uv |

### Postgres logins

| Login | Rights | Used by |
| --- | --- | --- |
| `atomic` | Owns the `atomic` database; creates its own schemas and migrates them | the runtime |
| `atomic_reader` | `SELECT` on the schemas and tables `atomic` creates, nothing else; read-only transactions and a 15-second statement timeout by default | Grafana's Durable runs dashboard |
| `atomic_monitor` | `pg_monitor`; may connect to `atomic` and `postgres` but read no tables | the collector's Postgres metrics |
| `postgres` | Superuser | setup only |

atomic finds its database through `ATOMIC_DATABASE_URL`, never
`DATABASE_URL`, which belongs to the application atomic works on.

### State

| Volume | Holds | If lost |
| --- | --- | --- |
| `atomic-state` | Today: atomic's durable database (`atomic.db`). Also Codex's home and state database, attempt worktrees, the local artifact store | Today, everything durable. Back it up |
| `postgres-data` | Once atomic stores workflows in Postgres: durable workflows and the work graph. Then `atomic-state` holds only transcripts and worktrees | Everything durable, from then on |
| `prometheus-data`, `tempo-data`, `loki-data`, `grafana-data` | Telemetry and Grafana's own settings | History only |

## Security notes

- Every published port is bound to 127.0.0.1. Grafana, Prometheus and the
  collector have no TLS here, and the collector accepts OTLP without
  authentication. Put a reverse proxy with TLS and sign-in in front before
  exposing any of them.
- The `atomic` container runs with `seccomp=unconfined` and
  `apparmor=unconfined`. See the first open question below.
- Atom commands can read this container's environment, including the
  database password, and every file in the `atomic-state` volume. See open
  question 8.
- `atomic_reader` can read every column of atomic's tables, including
  workflow inputs and outputs. The dashboards select only statuses, names
  and timestamps, but **any Grafana user**, viewers included, can send their
  own SQL through the datasource. Give Grafana accounts only to people who
  may see that data.
- The collector drops GenAI message content and Codex's tool arguments, tool
  output and prompts from span attributes, span events and log attributes,
  as a backstop. It does not inspect log bodies. Content capture is atomic's
  decision and is off by default.

## Open questions

Running atomic in a container surfaces these. Each is a question for atomic's
design, not just for this deployment, and each names the beads issue in
atomic's work graph that tracks it (`bd show <id>` in an atomic checkout).

1. **Codex's sandbox in a container** (`atomic-hfve.1`). Codex runs atom
   commands in a bubblewrap sandbox, which needs user namespaces and mounts.
   Docker's default seccomp profile refuses the first (`bwrap: No permissions
   to create a new namespace`), and its default AppArmor profile refuses the
   second, so this deployment lifts both for the atomic container. It was
   tested on a host with SELinux enforcing and Docker running without an
   LSM; hosts that restrict unprivileged user namespaces themselves (Ubuntu
   24.04 and later) need that allowed as well. Even with both lifted,
   bubblewrap cannot mount a fresh `/proc` here (`Can't mount proc on /proc:
   Operation not permitted`) and has to bind the container's own; whether
   Codex's sandbox does that by itself is not yet confirmed by a real turn.
   The choices are a seccomp and AppArmor profile that allow only what
   bubblewrap needs, running atom commands in a separate runner container and
   relying on its boundary, or a sandboxed runtime such as gVisor. All of
   them affect every container and Kubernetes deployment.

   **The split is planned.** This deployment runs the control plane and atom
   commands in one container, so the relaxed profiles, the project's
   toolchains and the commands sit beside the database credentials and every
   run's state. atomic plans to run them apart: `atomic serve` and
   `atomic runner` as separate processes with their own images (M24,
   `atomic-a6bh`, `atomic-kyuj`), and atom commands on a remote environment
   through Codex's exec-server, with the thread and checkpoints staying in
   the control plane (M20, `atomic-7jyf`). Once those land, this deployment
   becomes an `atomic` service built from the runtime image, with default
   profiles and no toolchains, and a runner service built from the project
   image, with no credentials and no route to Postgres.
2. **Executor identity and versions** (`atomic-ctd6.1`; upgrades: no bead
   yet). When atomic recovers unfinished runs
   at start-up, the durable engine takes only runs owned by its own executor
   ID, and the ID defaults to `local` when unset. Two processes with the
   default would recover each other's live runs; a process whose ID changes
   on restart (a random container or pod name) would never recover its own.
   This deployment sets `DBOS__VMID`, the variable durare reads, to a stable
   value; atomic should expose and require this as its own setting. Recovery
   also takes only runs written by the same application version, which
   defaults to a hash of the binary: after staging a new release, runs left
   unfinished by the old one are not recovered. atomic has to decide how an
   upgrade carries unfinished runs across.
3. **Health and readiness** (`atomic-exnm`). atomic has no health or
   readiness endpoint yet, so the service has no health check and nothing can
   wait for it.
4. **One replica only** (`atomic-oqff`, `atomic-2ts7`). Codex's state
   database and the attempt worktrees
   live in the `atomic-state` volume, which ties the runtime to one host. Even
   a restart that briefly runs two copies, as orchestrators do on update, needs
   the ownership fencing that multi-worker deployments need.
5. **The project checkout's owner** (`atomic-hfve.2`). atomic runs as uid
   10001 and the
   project is a bind mount owned by the host user. git refuses a repository
   owned by another user ("dubious ownership"), and atomic cannot write the
   checkout's `.git` for worktrees and commits. Either atomic works on a
   clone it owns, or the container runs as the checkout's owner. The `z`
   mount label also relabels the whole project tree on SELinux hosts.
6. **Counters that start above zero** (no bead yet). Prometheus computes
   `increase()` from
   two samples, so a counter whose first export is already above zero (three
   runs recovered as the process starts) counts as nothing. atomic should
   export its counters at zero for the series it knows at start-up. The
   Durable runs dashboard counts recovered runs from the database meanwhile.
7. **The command** (`atomic-0lv`). `atomic serve` does not exist yet, so the
   container exits with a usage error and restarts. Set `ATOMIC_COMMAND` to
   try other commands until it does.
8. **What atom commands can see** (`atomic-xi0.1`). Codex gives each command
   the environment of the process that runs it, and its default policy at
   atomic's pinned revision filters nothing, not even names containing
   `KEY`, `SECRET` or `TOKEN`. atomic sets no policy, so every atom command,
   and through its output the model, can read this container's environment,
   including `ATOMIC_DATABASE_URL` with its password. The sandbox also leaves
   the whole filesystem readable, so commands can read the `atomic-state`
   volume: atomic's database, Codex's home and other runs' worktrees. With
   no shell network a command cannot send any of it anywhere itself, but its
   output reaches the model and from there commits and messages. This is
   from reading Codex's source, not yet seen in a live turn. atomic should
   pass commands only core variables and those their atom declares; until
   then, keep model keys and other secrets out of this container's
   environment where you can.

## Stop and reset

```sh
docker compose down          # keep the data
docker compose down -v       # delete every volume, durable runs included
```

## Troubleshooting

- **Loki refuses logs** with `Ingester is shutting down` or a disk warning:
  Loki stops accepting writes when the disk holding its data is more than 90%
  full (`LOKI_DISK_FULL_THRESHOLD`). Free disk space, or raise the threshold on
  a test machine. Logs queue in the collector and are retried.
- **An empty dashboard.** Open **Telemetry pipeline** first: it shows whether
  anything is arriving. Then check [`../observability/metrics.md`](../observability/metrics.md)
  for the metric the panel reads; atomic may not export it yet.
- **SELinux.** Bind mounts carry the shared `z` label so they work on hosts
  that enforce SELinux for containers.
