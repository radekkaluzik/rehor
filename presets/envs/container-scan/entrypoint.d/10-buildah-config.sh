#!/bin/bash
# Buildah rootless config — runs as botuser at container startup
# vfs storage driver (no kernel module needed, works in OpenShift)
command -v buildah &>/dev/null || exit 0

export BUILDAH_ISOLATION=chroot

CONTAINERS_DIR="$HOME/.config/containers"
if [ ! -f "$CONTAINERS_DIR/storage.conf" ]; then
    mkdir -p "$CONTAINERS_DIR" "$HOME/.local/share/containers"
    printf '[storage]\ndriver = "vfs"\n' > "$CONTAINERS_DIR/storage.conf"
    printf '[registries.search]\nregistries = ["registry.access.redhat.com", "quay.io", "docker.io"]\n' \
        > "$CONTAINERS_DIR/registries.conf"
fi
