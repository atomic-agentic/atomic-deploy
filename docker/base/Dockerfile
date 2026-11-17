# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# SPDX-License-Identifier: Apache-2.0
#
# A project image with nothing added to atomic's runtime image. Atom commands
# find git and the base system, and no language toolchain. Use it for a
# project whose atoms only read and write files, or as the starting point for
# a project image of your own; ../python is the default.
#
# atomic-runtime is not pulled from a registry: the deployment passes its
# runtime image as this build's atomic-runtime context (see ../README.md).

FROM atomic-runtime
