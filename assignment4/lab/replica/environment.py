"""Record the actual server environment without API keys or process arguments."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import torch

result={'python':sys.version,'torch':torch.__version__,'torch_cuda':torch.version.cuda,
        'device':torch.cuda.get_device_name(0),
        'packages':{name:importlib.metadata.version(name) for name in ['sglang','flashinfer-python','sglang-kernel']},
        'cuda_home':os.environ.get('CUDA_HOME')}
for name,command in [('gpu',['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total,power.limit,clocks.max.sm,clocks.max.memory','--format=csv,noheader']),('compiler',['nvcc','--version'])]:
    p=subprocess.run(command,capture_output=True,text=True);result[name]={'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
print(json.dumps(result,indent=2))
