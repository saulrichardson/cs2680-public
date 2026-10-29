#!/bin/bash
set -euo pipefail
ROOT=${A4_REPLICA_ROOT:-/opt/a4-replica}
cd "$ROOT"
mkdir -p evidence weights
rm -f gpu-bootstrap.complete evidence/gpu-image.id
# These host-clock receipts bracket the actual subprocesses; no credentials or
# command arguments enter the timing evidence. Failed stages retain their status.
measure() {
  local stage=$1 started status=0
  shift
  started=$(python3 -c 'import json,time,datetime; print(json.dumps({"monotonic":time.monotonic(),"utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}))')
  "$@" || status=$?
  python3 - "$stage" "$started" "$status" <<'PY'
import datetime,json,sys,time
start=json.loads(sys.argv[2]); end=time.monotonic()
with open('evidence/gpu-bootstrap-stages.jsonl','a') as output:
    output.write(json.dumps({'stage':sys.argv[1],'started_at':start['utc'],
        'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'started_monotonic':start['monotonic'],'finished_monotonic':end,
        'elapsed_s':end-start['monotonic'],'exit_code':int(sys.argv[3])})+'\n')
PY
  return "$status"
}
nvidia-smi -q > evidence/nvidia-smi.txt
IMAGE_RECEIPT=$(mktemp evidence/gpu-image.id.XXXXXX)
trap 'rm -f "$IMAGE_RECEIPT"' EXIT
BUILD_TAG="a4-course-replica:bootstrap-${IMAGE_RECEIPT##*.}"
if test -f image-snapshot.json; then
  # Explicit frozen artifact only. Fail closed on mismatch, never rebuild the
  # measured cache arm. Actual software probe and checkpoint fetch still follow.
  MODE=$(cat image-materialization)
  if test "$MODE" = snapshot; then
    measure image-cache-verification sudo python3 image_snapshot.py host-verify --descriptor image-snapshot.json --recipe Dockerfile.gpu --receipt evidence/image-snapshot-verification.json > "$IMAGE_RECEIPT"
  elif test "$MODE" = archive; then
    if python3 -c 'import json,sys; sys.exit(0 if "object" in json.load(open("image-snapshot.json"))["archive"] else 1)'; then
      measure image-object-download python3 image_object.py download --descriptor image-snapshot.json --capability image-object.read-capability.json --output image.tar --receipt evidence/image-object-download.json
    fi
    measure image-archive-load sudo python3 image_snapshot.py host-load --descriptor image-snapshot.json --archive image.tar --recipe Dockerfile.gpu --receipt evidence/image-archive-verification.json > "$IMAGE_RECEIPT"
  else
    echo 'Unsupported image materialization' >&2; exit 1
  fi
  IMAGE=$(cat "$IMAGE_RECEIPT")
else
  # Some stores return a config digest in iidfile but a runnable manifest ID in
  # inspect. Resolve the unique build tag once, then use only that exact ID.
  measure build-image sudo docker build --iidfile "$IMAGE_RECEIPT" -f Dockerfile.gpu -t "$BUILD_TAG" .
  mv "$IMAGE_RECEIPT" evidence/gpu-build.id
  printf '%s\n' "$BUILD_TAG" > evidence/gpu-build-tag
  IMAGE=$(sudo docker image inspect "$BUILD_TAG" --format '{{.Id}}')
fi
[[ "$IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]]
sudo docker image inspect "$IMAGE" > evidence/image.json
measure software-probe sudo docker run --rm --gpus all --entrypoint python3 "$IMAGE" -c 'import sys,json,torch,importlib.metadata as m; print(json.dumps({"python":sys.version,"torch":torch.__version__,"torch_cuda":torch.version.cuda,"device":torch.cuda.get_device_name(0),"packages":{p:m.version(p) for p in ["sglang","flashinfer-python","sglang-kernel"]}},indent=2))' > evidence/software.json
measure checkpoint-fetch sudo docker run --rm --gpus all --entrypoint python3 -v "$ROOT/weights:/weights" "$IMAGE" -c 'from huggingface_hub import snapshot_download; print(snapshot_download(repo_id="nvidia/Qwen3.6-35B-A3B-NVFP4",revision="1355db6a052410cfd62085d94b58866fd0f2c3c5",local_dir="/weights/model"))'
printf '%s\n' "$IMAGE" > "$IMAGE_RECEIPT"
mv "$IMAGE_RECEIPT" evidence/gpu-image.id
touch gpu-bootstrap.complete
