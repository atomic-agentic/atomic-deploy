# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# The default project image: atomic's runtime image with nothing added. Atom
# commands find git and the base system, and no language toolchain. For a
# project whose atoms build or test code, point ATOMIC_PROJECT_IMAGE_DIR at a
# directory with a Dockerfile that installs its toolchain, such as ../rust.
#
# atomic-runtime is not pulled from a registry: compose.yaml passes the image
# it builds from ../../atomic as this build's atomic-runtime context.

FROM atomic-runtime
