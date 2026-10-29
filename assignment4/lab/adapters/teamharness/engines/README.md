# Python harness engine assets

`install_engine(package_directory, 'stock-c5')` installs only `engine/engine.json`,
`engine/flags.txt` and executable `engine/launch.sh`. It returns the profile
metadata for the enclosing build receipt. The maintained builder owns profile
selection; its `None` default retains runtime-only packaging. An existing engine
directory is refused rather than overwritten.

`stock-c5` preserves the exact engine bytes and file modes from
`a4-stock-ahe-c5-under50mb-20261028.zip`, SHA-256
`5702dd2892d031acc99065927bfa7d05198151f59f8cd503c23e6a84a1b111a4`.
The adjacent profile JSON records all three file hashes. The name identifies the
proven source package; these engine files do not select task concurrency.
No AHE solver, NexAU package, interpreter bundle or evidence archive is copied.

`stock-c5-headroom` derives from those retained assets and changes only static
memory fraction from 0.90 to 0.85. The shared-GPU Python rehearsal uses this
profile: actual planning traffic on the 32 GiB AWS GPU caused a late Triton kernel
load followed by a failed 512 MiB CUDA allocation with about 0.49 GiB free at
0.90. The original `stock-c5` profile retains its exact source bytes for provenance.
The derived profile's separate manifest records its actual flags and parent;
its live qualification belongs to the rehearsal evidence, not the parent's
historical acceptance.

The checkpoint is
`nvidia/Qwen3.6-35B-A3B-NVFP4@1355db6a052410cfd62085d94b58866fd0f2c3c5`.
The profile retains context length 262144, static memory fraction 0.90, seed
20261028, `extra_buffer` Mamba cache, FlashInfer attention and MoE backends,
PyTorch sampling, bfloat16 SSM state, Qwen reasoning/tool parsers, metrics and
cache reporting. It adds no speculative-decoding flags or request-concurrency cap.

The launcher delegates to the course-provided `a4-serve`, with the quoted
`$A4_ENGINE_DIR/flags.txt` path. The course controls model path, served name,
host, port and API key. The accepted lab environment used SGLang 0.5.20; the
profile records its retained wheel/source identities. The engine directory does
not install or enforce that framework version. Use the course environment or
a matching qualified lab environment, with the fixed weights available.

Local qualification checks asset identity, shell syntax, references, exact
parameters and the actual executable's argument/exit behavior using an inert
`a4-serve` fixture. Optional source-ZIP and retained-wheel checks inspect local
files only. These checks do not establish GPU fit, Python repair outcomes or a
new performance gain. Historical engine evidence is reused; no fresh GPU launch
is part of this installation.

Run the focused checks from the repository's assignment4 directory:

```sh
PYTHONPATH=test/teamharness:. python3 -m unittest test_engine_profiles
```

Set `TEAMHARNESS_ENGINE_SOURCE_ZIP` to the original local ZIP and
`TEAMHARNESS_SGLANG_WHEEL` to the retained 0.5.20 wheel to additionally compare
source members and inspect pinned argument declarations. The latter is a source
compatibility check, not an import or GPU execution of SGLang.
