# probes/ — standalone gfx1151 mechanism probes

Single-node, no model, no peer, no serve. Each answers one question about the
hardware before any transport or engram code is written, per the discipline in
`handoff/RUN3-BRIEF.md` §14.14: *every optimization argued from reading code lost;
every one measured first won.*

Results are banked in **RUN3-BRIEF §16**.

## Build and run

All probes build with the container's hipcc and need the ROCm libs on the loader path:

```sh
GPU="--device /dev/kfd --device /dev/dri --security-opt seccomp=unconfined --ipc=host --group-add keep-groups"
LIBS=/opt/venv/lib/python3.12/site-packages/_rocm_sdk_core/lib:/opt/venv/lib/python3.12/site-packages/_rocm_sdk_devel/lib

podman run --rm $GPU -v "$PWD/probes:/w:z" flashnext:dev bash -lc '
  export LD_LIBRARY_PATH='"$LIBS"':$LD_LIBRARY_PATH
  hipcc -O3 --offload-arch=gfx1151 -lpthread /w/hip_atomic_doorbell_soak.cpp -o /tmp/soak
  /tmp/soak 25800 57 mapped checkpayload'
```

Without `LD_LIBRARY_PATH` the binaries die on `libamdhip64.so.7`.

## `hip_atomic_doorbell_soak.cpp` (ours)

Settles RUN3-BRIEF §15.2: is a GPU→host **in-kernel system-scope atomic** doorbell
viable on gfx1151 under sustained load? ds4's documented failure indicts
`hipStreamWriteValue64` *packet submission*, which is a different mechanism, and they
left this question open.

```
./soak [gates=25800] [delay_us=57] [mapped|device] [checkpayload]
```

25,800 gates = 300 tokens x 86, ds4's production schedule. Their failure surfaced at
seq 1306, so **anything under ~2000 iterations proves nothing**.

Result (2026-08-31, RUN3-BRIEF §16.2): **PASS on both allocators** — zero missed
arrivals, zero payload mismatches over 25,800. Mechanism cost **4.49 us/gate** with a
`hipHostMalloc(Mapped)` flag versus **119 us/gate** with a `hipMalloc` flag, and versus
**43-45 us/gate** for ds4's ordered host callback.

Two things it is easy to get wrong, both learned the hard way here:

- **The producer must be ONE block.** `__syncthreads()` is a block-wide barrier only, so
  a multi-block grid rings the doorbell while sibling blocks are still writing the
  payload. This reports as payload corruption and is not one.
- **Never validate a float payload across the host/device boundary.** HIP contracts
  `a + b*c` into an FMA on device but not on host, so the two disagree by rounding and
  the probe reports mismatches that are not there. The payload here is integer.

## ds4 probes — not vendored

The eleven probes from `ds4-strix-halo-tp-odinlink` are third-party and are deliberately
**not** copied into this tree. Stage them from the clone:

```sh
R=~/Downloads/ds4-strix-halo-tp-odinlink
cp $R/scripts/{rocm_host_copy_probe.cu,t0_hipmalloc_host_probe.cpp,\
t2_payload_visibility_probe.cpp,t3_gate_signal_probe.cpp,t4_null_stream_gate_probe.cpp,\
t5_gate_stream_fix_probe.cpp,t6_bandwidth_probe.cpp,hip_host_callback_gate_probe.cpp,\
hc_cooperative_grid_probe.cu} .

# the two hip_graph probes are NOT on main:
git -C $R show origin/research/q4k-hipgraph-20260818:scripts/hip_graph_default_stream_probe.cu > hip_graph_default_stream_probe.cu
git -C $R show origin/research/q4k-hipgraph-20260818:scripts/hip_graph_launch_ceiling_probe.cu > hip_graph_launch_ceiling_probe.cu
```

Note `t3`-`t6` are `.cpp`, not `.cu` as RUN3-BRIEF §14.12/§15.6 record them. All eleven
compile unmodified against ROCm 7.14 / gfx1151.

**Run `rocm_host_copy_probe` in both modes.** Exactly one prints PASS; which one tells you
whether the stack has the 7.14 overlapping-host-registration behavior. Ours does — see
§16.1, and the engram path needs the 64 MiB pinned-bounce fallback because of it.
