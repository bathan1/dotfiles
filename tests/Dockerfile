ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git openssh-client python3 shellcheck sudo util-linux xz-utils zsh \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash developer \
    && printf 'developer ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/developer

COPY setup.sh /opt/dev-setup/setup.sh
COPY tests /opt/dev-setup/tests
USER developer
WORKDIR /opt/dev-setup
CMD ["bash", "tests/smoke.sh"]
