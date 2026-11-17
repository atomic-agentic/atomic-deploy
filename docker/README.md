# Project images

Atom commands run inside atomic's container, in Codex's sandbox. An atom that
is told to run the tests needs the project's interpreter, packages and test
tools there. Model-backed atoms have no shell network by default, so all of it
has to be in the image already: a turn cannot download any of it.

A deployment therefore runs a **project image**: atomic's runtime image with
the project's toolchains added. The images here are shared by every
deployment, as [`../observability`](../observability) is.

| Directory | Adds | Image size |
| --- | --- | --- |
| [`python`](python) | uv, the Python in the project's `.python-version` and every package in its `uv.lock` | about 730 MB with the Compose example project |
| [`base`](base) | Nothing | about 480 MB |

## What a project image is

A directory holding a Dockerfile, built with two named build contexts that
the deployment supplies:

| Context | What it is |
| --- | --- |
| `atomic-runtime` | atomic's runtime image, built from a release archive. The Dockerfile starts `FROM atomic-runtime` |
| `project` | The project directory, for files such as a lockfile. Take them with `COPY --from=project <files>`, which sends only the files it names |

```dockerfile
FROM atomic-runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends <packages> \
    && rm -rf /var/lib/apt/lists/*
USER atomic
```

[`../docker-compose`](../docker-compose) shows the wiring: the runtime image is
a build-only service, and `ATOMIC_PROJECT_IMAGE_DIR` picks the project image.

A project can keep its own Dockerfile in its repository instead. The
directory is the build context, so use a small one such as `.atomic/image/`,
not the repository root. Install only what the project's atoms run, pin
versions, and check every download against a checksum you have verified, as
the Python image does for uv. End as `USER atomic`: the runtime expects to run
as uid 10001.

## The Python image

For a project managed with [uv](https://docs.astral.sh/uv/). It needs
`pyproject.toml`, `uv.lock` and `.python-version` in the project. With the
Compose example project, an atom can run `uv run pytest`, `uv run ruff check`
and `uv run ruff format --check` in its worktree, offline. This was checked
under bubblewrap set up the way Codex's workspace-write sandbox is: the root
read-only, the worktree and `/tmp` writable, and no network.

- **uv** 0.13.6 comes from its GitHub release, checked against a pinned
  checksum. The Dockerfile says how to move to another release.
- **Python** is installed by uv, as root, into `/opt/uv/python`, read-only to
  atom commands. It is the version in the project's `.python-version`, and
  `python3.<minor>` is linked into `/usr/local/bin`.
- **Packages**, every one in `uv.lock` with all groups and extras, go into
  uv's cache at `/tmp/uv-cache` when the image is built. uv will not use a
  cache it cannot write, and `/tmp` is writable in the workspace-write
  sandbox. A turn's `uv run` builds the worktree's `.venv` from the cache, and
  builds the project with uv's own `uv_build` backend, without a download.
- **uv stays offline at run time** through `/etc/uv/uv.toml`: a file rather
  than `UV_*` variables, so it does not depend on the environment the sandbox
  passes on.

What follows from that:

- **A dependency added during a turn cannot be installed.** `uv add` fails,
  with uv's hint that the network is disabled. Add dependencies outside
  atomic, commit `uv.lock`, and rebuild; or give the atoms that need it
  network access to the package index.
- **Read-only atoms cannot use `uv run`.** They have no writable `/tmp` for the
  cache and no writable worktree for the `.venv`. Atoms that run tests need
  `workspace-write` anyway.
- **The cache is shared and writable.** Every attempt's commands can write
  `/tmp/uv-cache`, so one attempt could change what a later one installs. The
  cache goes back to the image's copy when the container is recreated from
  the image, not when it restarts.

## Why not codex-universal

[codex-universal](https://github.com/openai/codex-universal), the image Codex
cloud starts from, is the other way to do this: every toolchain in several
versions, picked at start. It is not used here. It is about 11 GB compressed,
runs as root with its toolchains under `/root`, has neither bubblewrap nor
tini, and its builds are not reproducible (an unpinned base and `curl | sh`
installers). It may suit a separate runner container
([open question 1](../docker-compose/README.md#open-questions)) for a project
that already has a Codex cloud environment.
