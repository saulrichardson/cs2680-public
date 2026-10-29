#!/bin/bash
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-buildx git python3-venv python3-pip curl zstd
sudo usermod -aG docker ubuntu
sudo systemctl enable --now docker
curl -LsSf https://astral.sh/uv/install.sh | sh
mkdir -p /opt/a4-replica/evidence
sudo docker info > /opt/a4-replica/evidence/docker-info.txt
# Course dependency-cache builds use RUN --mount and cannot use the legacy builder.
sudo docker buildx version > /opt/a4-replica/evidence/buildx-version.txt
touch /opt/a4-replica/cpu-bootstrap.complete
