# Third-party code and licenses

Third-party sources are **fetched at pinned revisions at build time, not
redistributed here** — what this repository itself ships is at most a
derivative patch against them (each patch is licensed like the code it
modifies). Original code in this repository is licensed under
[Apache-2.0](LICENSE). No GPL code enters this tree (the RDMA transport
subtree of the reference implementation is excluded from scope entirely).

| Component | Upstream | License | What this repo ships |
|---|---|---|---|
| vLLM (+ PR #53896, #54129 content) | github.com/vllm-project/vllm; fork base `8e4e036` | Apache-2.0 | Derivative patches under `patches/`, mirrored as commits on github.com/mecattaf/vllm branch `flashnext`; provenance per file in `patches/MANIFEST.md` |
| Cherry-picked open PRs #46012 #40963 #51511 #46110 | github.com/vllm-project/vllm pull requests | Apache-2.0 | Carried as fork commits with `Cherry-picked-from:` trailers; nothing redistributed here |
| ds4-vllm instruments and discipline | github.com/AlexKGwyn/ds4-vllm @ `a8f620d` | Apache-2.0 | Adapted copies under `container/rootfs/` (`fn_synctrace.py`, `fn_expert_union.py`, `fn_offload_batch.py`) retaining upstream notice headers; the packaging-test discipline reimplemented in `tests/` |
| amd-strix-halo-vllm-toolboxes container recipe | github.com/kyuz0/amd-strix-halo-vllm-toolboxes @ `23cb726` | MIT | `container/Containerfile` is an adaptation; MIT notice retained in that file's header |
| nix-strix-halo | github.com/hellas-ai/nix-strix-halo @ `f0f2048` | no license published | Consumed as a Nix flake input only; nothing redistributed, nothing copied |
| AMD ROCm wheels (torch/triton/vision, rocm 7.14.0) | repo.amd.com/rocm/whl-multi-arch | per-wheel licenses | Fetched at container build; nothing redistributed |

**Model weights** (`Qwen/Qwen3.8-Flash-Next-FP8`) are not included and are
governed by their own license on Hugging Face.
