ARG NETBOX_IMAGE=docker.io/netboxcommunity/netbox:v4.6.2
FROM ${NETBOX_IMAGE}

ARG ALEMBIC_VERSION=v0.10.0
ARG TARGETARCH

USER root
# the alembic release attaches a static linux binary per architecture.
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) target=x86_64-unknown-linux-gnu ;; \
      arm64) target=aarch64-unknown-linux-gnu ;; \
    esac; \
    name="alembic-${ALEMBIC_VERSION}-${target}"; \
    curl -fsSL "https://github.com/cyberwitchery/alembic/releases/download/${ALEMBIC_VERSION}/${name}.tar.gz" \
      | tar -xz -C /usr/local/bin --strip-components=1 "${name}/alembic"; \
    alembic --version; \
    mkdir -p /opt/alembic-work && chown netbox /opt/alembic-work
USER netbox
