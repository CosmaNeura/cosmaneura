# Native ARM64 avoids unsupported .NET/QEMU execution on the Apple Silicon host.
FROM ubuntu:24.04@sha256:19cad59657037cffa0ac9a48107019b768900ff1071140965827513ac866bd5e
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git jq sudo unzip zsh build-essential libpq-dev \
    libicu74 libssl3t64 libkrb5-3 zlib1g python3 python3-venv gh \
    docker.io docker-compose-v2 docker-buildx && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/azure-cli && \
    /opt/azure-cli/bin/pip install --no-cache-dir azure-cli==2.89.0 && \
    ln -s /opt/azure-cli/bin/az /usr/local/bin/az
# Procfile and deployment tests rely on the same yq CLI formerly preinstalled
# on hosted Ubuntu. Pin the official asset before any credential is present.
RUN curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error \
      https://github.com/mikefarah/yq/releases/download/v4.48.1/yq_linux_arm64 \
      --output /usr/local/bin/yq && \
    printf '%s  /usr/local/bin/yq\n' 0e46b5b926a9e57c526fa2bd8f8e38b7e17fbf6e2403ff1741f3b268e3363a9e | sha256sum --check --strict && \
    chmod 0755 /usr/local/bin/yq
ARG RUNNER_VERSION=2.338.2
ARG RUNNER_SHA256=670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945
RUN mkdir -p /opt/runner && \
    curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz" \
      --output /tmp/runner.tar.gz && \
    printf '%s  /tmp/runner.tar.gz\n' "$RUNNER_SHA256" | sha256sum --check --strict && \
    tar -xzf /tmp/runner.tar.gz -C /opt/runner && rm /tmp/runner.tar.gz
RUN useradd --create-home --uid 1001 --shell /bin/bash runner && \
    printf 'runner ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/runner && \
    chmod 0440 /etc/sudoers.d/runner && chown -R runner:runner /opt/runner && \
    mkdir -p /private/tmp && chmod 1777 /private/tmp
RUN apt-get update && apt-get install -y --no-install-recommends socat && \
    rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/local-runner-entrypoint
COPY job-started.sh /usr/local/bin/local-release-job-started.sh
RUN chmod 0755 /usr/local/bin/local-runner-entrypoint /usr/local/bin/local-release-job-started.sh
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/local-release-job-started.sh
USER runner
WORKDIR /opt/runner
ENTRYPOINT ["/usr/local/bin/local-runner-entrypoint"]
