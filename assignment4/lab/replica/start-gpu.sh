#!/bin/bash
set -euo pipefail
ROOT=${A4_REPLICA_ROOT:-/opt/a4-replica}
cd "$ROOT"
: "${A4_EXPERIMENT:?Path of frozen experiment required}"
mkdir -p evidence kernel-cache
if ! test -f gpu-bootstrap.complete || ! test -f evidence/gpu-image.id; then
  echo 'Run bootstrap-gpu.sh successfully before starting the server' >&2
  exit 1
fi
IMAGE=$(cat evidence/gpu-image.id)
[[ "$IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]]
# Refuse a missing image instead of silently rebuilding a different environment.
test "$(sudo docker image inspect "$IMAGE" --format '{{.Id}}')" = "$IMAGE"
sudo docker image inspect "$IMAGE" > evidence/replica-image.json
sudo docker run --rm --gpus all --entrypoint python3 \
  -v "$ROOT/environment.py:/environment.py:ro" "$IMAGE" /environment.py > "${A4_RUNTIME:-evidence/runtime.json}"
NETWORK=a4-server-private
if ! sudo docker network inspect "$NETWORK" >/dev/null 2>&1; then
  sudo docker network create --internal "$NETWORK" >/dev/null
fi
test "$(sudo docker network inspect "$NETWORK" --format '{{.Internal}}')" = true
# The model has no internet route. A separate byte relay exposes only this server,
# analogous to the course's transport boundary; it cannot route arbitrary targets.
MODEL=$(sudo docker run -d --gpus all --ipc=host --network "$NETWORK" --name a4-stock-server \
  -v "$A4_EXPERIMENT/course/engine:/engine:ro" \
  -v "$ROOT/weights/model:/model:ro" \
  -v "$ROOT/serve.key:/run/serve.key:ro" \
  -v "$ROOT/a4-serve:/usr/local/bin/a4-serve:ro" \
  -v "$ROOT/kernel-cache:/root/.cache" \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e A4_ENGINE_DIR=/engine -e A4_MODEL_PATH=/model \
  -e A4_SERVED_MODEL_NAME=qwen3.6-35b-a3b-fp4 \
  -e A4_HOST=0.0.0.0 -e A4_PORT=30000 -e A4_KEY_FILE=/run/serve.key \
  --entrypoint bash "$IMAGE" /engine/launch.sh)
IP=$(sudo docker inspect "$MODEL" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
RELAY=$(sudo docker run -d --name a4-server-transport --network bridge -p 30000:30000 \
  -v "$ROOT/tcp_forward.py:/relay.py:ro" \
  --entrypoint python3 "$IMAGE" /relay.py --upstream "$IP")
trap 'sudo docker stop "$RELAY" >/dev/null 2>&1 || true' EXIT
sudo docker network connect "$NETWORK" "$RELAY"
sudo docker inspect "$MODEL" "$RELAY" > evidence/server-containers.json
sudo docker logs -f "$MODEL" &
LOG_PID=$!
STATUS=$(sudo docker wait "$MODEL")
wait "$LOG_PID" || true
exit "$STATUS"
