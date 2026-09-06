# Building Release Artifacts

This guide covers every release artifact that can be built from the repository:
the standalone Windows executable and Docker images. Run all commands from the
repository root.

## Windows executable

### Prerequisites

- Windows with Python 3.11 or newer
- The project dependencies
- [PyInstaller 6.21.0](https://pyinstaller.org/)

Install the build dependencies. `requirements-windows.txt` includes
`requirements.txt` plus `pywebview`, which powers the executable's desktop
window and is not needed by Docker or a plain source checkout:

```powershell
py -m pip install -r requirements-windows.txt
py -m pip install pyinstaller==6.21.0
```

Build the executable:

```powershell
.\build_windows.bat
```

The build produces `dist\TwitchChannelPointsMiner.exe`. Copy it to a writable
directory before running it. Launching it opens a desktop window with a
Dashboard tab (the analytics dashboard, embedded) and a Console tab (the
miner's live log output, including the Twitch sign-in prompt). The
executable's first-launch configuration and upgrade behavior are documented
in the [Windows section](README.md#windows) of the main README.

To build the per-user Windows installer after building the executable, install
[Inno Setup 6](https://jrsoftware.org/isinfo.php) and run:

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" `
  "/DMyAppVersion=<version>" windows_installer.iss
```

The installer is written to
`dist\TwitchChannelPointsMiner-<version>-Setup.exe`. It installs under the current
user's local application data, creates a Start menu shortcut, and offers an
optional desktop shortcut. This keeps the application directory writable for
the first-launch configuration file and does not require administrator access.
The installer also offers to prefill the Twitch username and watched channels
in a new configuration and to open the configuration guide. Existing
configurations are never overwritten.

Tagged releases that contain code changes run the same build automatically and
attach both a versioned portable zip and a
`TwitchChannelPointsMiner-<version>-Setup.exe` installer to the GitHub release.
See [the release workflow](.github/workflows/release-please.yml) for the
automated build definition.

## Docker images

This section explains how to build this project locally and publish it to a
Docker Hub repository. The tagging follows
[the Docker deployment workflow](.github/workflows/deploy-docker.yml).

Docker builds **images**. A container is created later when an image is run.

### Prerequisites

- Docker Engine or Docker Desktop
- A Docker Hub account
- Repositories named `twitch-channel-points-miner` and
  `twitch-channel-points-miner-v2` in the namespace you will publish to
- A Docker Hub personal access token with read and write permission

The official images use the `zacharmstrong` namespace.

For a multi-platform build, Docker must also have the Buildx plugin. Docker
Desktop includes Buildx; recent Docker Engine installations normally include
it as the `docker-buildx-plugin` package.

### Set the image name and version

Replace the example values with your Docker Hub username or organization and
the version you are publishing:

```sh
export DOCKERHUB_NAMESPACE="your-dockerhub-username"
export DOCKERHUB_USERNAME="your-dockerhub-username"
export IMAGE="${DOCKERHUB_NAMESPACE}/twitch-channel-points-miner"
export LEGACY_IMAGE="${DOCKERHUB_NAMESPACE}/twitch-channel-points-miner-v2"
export VERSION="3.0.0"
```

`DOCKERHUB_NAMESPACE` may be an organization while `DOCKERHUB_USERNAME` is the
account that has permission to push to it.

### Log in to Docker Hub

```sh
docker login --username "${DOCKERHUB_USERNAME}"
```

Enter the personal access token when Docker asks for a password. Do not put the
token in this repository or directly in a shell command.

### Option 1: Build and push for the current platform

This is the quickest way to test the Dockerfile and publish an image for the
same CPU architecture as the development machine:

```sh
docker build --pull \
    --tag "${IMAGE}:${VERSION}" \
    --tag "${IMAGE}:latest" \
    --tag "${LEGACY_IMAGE}:${VERSION}" \
    --tag "${LEGACY_IMAGE}:latest" \
    .
```

Confirm that all local tags exist:

```sh
docker image inspect "${IMAGE}:${VERSION}"
docker image inspect "${IMAGE}:latest"
docker image inspect "${LEGACY_IMAGE}:${VERSION}"
docker image inspect "${LEGACY_IMAGE}:latest"
```

Push all tags:

```sh
docker push "${IMAGE}:${VERSION}"
docker push "${IMAGE}:latest"
docker push "${LEGACY_IMAGE}:${VERSION}"
docker push "${LEGACY_IMAGE}:latest"
```

This method publishes only the development machine's architecture. Use the next
option for an AMD64 and ARM64 release.

### Option 2: Build and push a multi-platform release

Create a reusable Buildx builder. This only needs to be done once:

```sh
docker buildx create \
    --name tcpm-builder \
    --driver docker-container \
    --use
docker buildx inspect --bootstrap
```

If the builder already exists, select it instead:

```sh
docker buildx use tcpm-builder
docker buildx inspect --bootstrap
```

Before building, check the `Platforms` line from the inspect command. It must
include `linux/amd64` and `linux/arm64`. If it lists only AMD64 and 386 on a
standalone Linux Docker Engine host, ARM QEMU/binfmt emulation has not been
registered. Docker's documented installation method is:

```sh
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

This pulls and runs a privileged helper that registers the ARM64 emulator on
the host. Review the [Docker multi-platform build documentation][docker-qemu]
before using it on a shared or production host. Afterward, recreate the builder
and inspect it again. Docker Desktop normally provides emulation automatically.

[docker-qemu]: https://docs.docker.com/build/building/multi-platform/#install-qemu-manually

Build AMD64 and ARM64 and push the version and `latest` tags directly to Docker
Hub:

```sh
docker buildx build --pull \
    --platform linux/amd64,linux/arm64 \
    --tag "${IMAGE}:${VERSION}" \
    --tag "${IMAGE}:latest" \
    --tag "${LEGACY_IMAGE}:${VERSION}" \
    --tag "${LEGACY_IMAGE}:latest" \
    --push \
    .
```

The `--push` flag is required because a multi-platform result cannot be loaded
as one image into the classic local Docker image store. Buildx publishes a
manifest that points to the image for each supported architecture.

### Verify the published images

Inspect the Docker Hub manifest and confirm that it lists AMD64 and ARM64:

```sh
docker buildx imagetools inspect "${IMAGE}:${VERSION}"
docker buildx imagetools inspect "${IMAGE}:latest"
docker buildx imagetools inspect "${LEGACY_IMAGE}:${VERSION}"
docker buildx imagetools inspect "${LEGACY_IMAGE}:latest"
```

You can also pull the image normally to test the variant for the current
machine:

```sh
docker pull "${IMAGE}:${VERSION}"
```

Run it using the configuration and volume mounts documented in the
[Docker section of the README](README.md#docker).

### Publishing another version

Change `VERSION`, then repeat the selected build and push commands:

```sh
export VERSION="1.0.1"
```

Only update `latest` when the new version should become the default Docker Hub
release. To publish a version without changing `latest`, remove both `latest`
tag arguments from the build command and do not push those tags.

### Troubleshooting

- `denied: requested access to the resource is denied`: check the namespace,
  repository name, token permissions, and the logged-in account.
- `multiple platforms feature is currently not supported`: select a Buildx
  builder that uses the `docker-container` driver.
- ARM builds fail before the Dockerfile runs: ensure
  `docker buildx inspect --bootstrap` reports `linux/arm64`. On a Linux host,
  QEMU/binfmt emulation may need to be installed by the system administrator.
  Do not start the multi-platform build until that platform is reported.
- `archive/tar: invalid tar header` while unpacking the base image: retry once
  in case the registry download was interrupted. If it happens again, recreate
  the disposable Buildx builder to discard its content store and cached layers:

  ```sh
  docker buildx rm tcpm-builder
  docker buildx create \
      --name tcpm-builder \
      --driver docker-container \
      --use
  docker buildx inspect --bootstrap
  ```

  Recreating this builder removes its build cache, but does not remove normal
  Docker images or running containers. Confirm that the inspect output includes
  ARM64 before retrying the build.

  If a fresh builder still fails on `WORKDIR`, or the error moves between ARMv7
  and ARM64, create a separate builder using BuildKit's `native` snapshotter.
  This avoids the failing overlay-backed cache-key scan. The BuildKit version is
  pinned here to keep the workaround reproducible:

  ```sh
  docker buildx create \
      --name tcpm-builder-native \
      --driver docker-container \
      --driver-opt image=moby/buildkit:v0.29.0 \
      --buildkitd-flags '--oci-worker-snapshotter=native' \
      --use
  docker buildx inspect tcpm-builder-native --bootstrap
  ```

  If `tcpm-builder-native` already exists, select it instead of creating it
  again:

  ```sh
  docker buildx use tcpm-builder-native
  docker buildx inspect tcpm-builder-native --bootstrap
  ```

  In the inspect output, confirm that the snapshotter label is `native` and the
  `Platforms` line includes ARM64. Then select this builder for the publish
  command:

  ```sh
  docker buildx build \
      --builder tcpm-builder-native \
      --pull \
      --platform linux/amd64,linux/arm64 \
      --tag "${IMAGE}:${VERSION}" \
      --tag "${IMAGE}:latest" \
      --push \
      .
  ```
- Docker Hub shows only one architecture: the image was probably built with
  `docker build` or without the full `--platform` list. Rebuild it with the
  multi-platform command above.
- The ARMv7 build spends a long time at `Preparing metadata (pyproject.toml)`
  and then reports a QEMU or compiler segmentation fault: pip is compiling
  NumPy from source because no compatible wheel is available. Publish AMD64 and
  ARM64 locally, or build ARMv7 on a native ARMv7 Buildx node.
