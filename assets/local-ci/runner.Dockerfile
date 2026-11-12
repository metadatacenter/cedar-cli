# The Linux runner local CI runs a workflow's Ubuntu legs in.
#
# GitHub's Ubuntu runner is a virtual machine whose image carries the tools the setup actions
# select: a JDK, Node and Python in a tool cache, Maven, Git and a C toolchain, and a `runner` user
# with passwordless sudo. This image offers the same to the setup actions the CEDAR workflows use,
# on the Ubuntu release the job's runner label names. Local CI passes that release as BASE, pinned
# by digest, and tags the image with a digest of this file and BASE, so a change to either builds
# a new image rather than reusing an old one.
#
# The tool cache sits at /opt/hostedtoolcache, as on GitHub. A job that names a container of its
# own gets the same tool cache mounted read-only at /__t, which is where GitHub mounts its runner's.
# A job's workspace and temporary directory live on the container's own file system, where GitHub
# keeps them, rather than on a directory mounted from macOS, which differs in case sensitivity, in
# path length and in how a few system calls behave.

# Local CI always passes BASE pinned by digest; the default only lets the file build by hand.
ARG BASE=ubuntu:26.04

FROM eclipse-temurin:17-jdk-noble@sha256:ec2c8050c856a00986776ddd94d4facc9e872430c27799108ca318a26f9f1887 AS java
FROM node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c AS node
FROM maven:3.9.14-eclipse-temurin-17@sha256:674de0df5a38cfe793a587829a181835f8af3ea1f556be7172e0aa547274bcc9 AS maven
FROM ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 AS uv

# The interpreters setup-python selects. uv fetches python-build-standalone builds, which run on
# any glibc Linux, and checks each download against the digest it carries for that release.
FROM ${BASE} AS python
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && UV_PYTHON_INSTALL_DIR=/opt/python uv python install 3.10 3.12 \
 && mkdir -p /opt/hostedtoolcache/python \
 && for version in 3.10 3.12; do \
      build=$(UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed \
              uv python find "${version}"); \
      cp -a "$(dirname "$(dirname "$(readlink -f "${build}")")")" "/opt/hostedtoolcache/python/${version}"; \
    done

FROM ${BASE}
ARG DEBIAN_FRONTEND=noninteractive

# What the workflows' run steps call on the runner beyond the tool cache. Git and a C toolchain
# build Git 2.34.1 from source; sudo mounts a noexec tmpfs and lets Playwright install the system
# libraries its browsers need. The CLI's release tests stub every GitHub CLI call but require the
# command to exist, and the native controller's safety tests read real processes with ps and lsof,
# as they can on GitHub's runner.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl file gh git gnupg iproute2 jq less libexpat1-dev \
      lsb-release lsof openssh-client procps psmisc python3 python3-venv rsync sudo tzdata unzip \
      util-linux wget xz-utils zip zlib1g-dev zstd \
 && rm -rf /var/lib/apt/lists/*

COPY --from=java /opt/java/openjdk /opt/hostedtoolcache/java/17
COPY --from=node /usr/local/bin/node /opt/hostedtoolcache/node/24.22.1/bin/node
COPY --from=node /usr/local/lib/node_modules /opt/hostedtoolcache/node/24.22.1/lib/node_modules
COPY --from=node /usr/local/include/node /opt/hostedtoolcache/node/24.22.1/include/node
COPY --from=python /opt/hostedtoolcache/python /opt/hostedtoolcache/python
COPY --from=maven /usr/share/maven /usr/share/maven
RUN cd /opt/hostedtoolcache/node/24.22.1/bin \
 && ln -s ../lib/node_modules/npm/bin/npm-cli.js npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js npx \
 && ln -s ../lib/node_modules/corepack/dist/corepack.js corepack \
 && ln -s /usr/share/maven/bin/mvn /usr/bin/mvn

# GitHub's runner user. Ubuntu's own images carry a user `ubuntu` at 1000, so the runner keeps
# GitHub's 1001.
RUN useradd --create-home --uid 1001 --shell /bin/bash runner \
 && echo 'runner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/runner \
 && chmod 0440 /etc/sudoers.d/runner \
 && chown -R runner:runner /opt/hostedtoolcache

ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    LANG=C.UTF-8
USER runner
WORKDIR /home/runner
