# Official Ubuntu 24.04 AMD64 base; all execution remains on the operator's Docker host.
FROM ubuntu:24.04@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d
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
      https://github.com/mikefarah/yq/releases/download/v4.48.1/yq_linux_amd64 \
      --output /usr/local/bin/yq && \
    printf '%s  /usr/local/bin/yq\n' 99df6047f5b577a9d25f969f7c3823ada3488de2e2115b30a0abb10d9324fd9f | sha256sum --check --strict && \
    chmod 0755 /usr/local/bin/yq
ARG RUNNER_VERSION=2.338.0
ARG RUNNER_SHA256=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
RUN mkdir -p /opt/runner && \
    curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
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
COPY job-started.sh /usr/local/bin/local-release-job-started
RUN chmod 0755 /usr/local/bin/local-runner-entrypoint /usr/local/bin/local-release-job-started
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/local-release-job-started
USER runner
WORKDIR /opt/runner
ENTRYPOINT ["/usr/local/bin/local-runner-entrypoint"]
