#!/usr/bin/env bash
# Ubuntu 22.04/24.04 serverda, shu papka ichida:  bash deploy.sh
set -e
apt-get update
apt-get install -y docker.io docker-compose-v2 unzip
systemctl enable --now docker
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> .env yaratildi. Uni to'ldiring:  nano .env   (so'ng yana: bash deploy.sh)"
  exit 1
fi
mkdir -p data
docker compose up -d --build
docker compose ps
echo ">>> Tayyor. Loglar:  docker compose logs -f"
