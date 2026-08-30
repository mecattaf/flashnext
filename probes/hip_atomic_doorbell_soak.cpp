// hip_atomic_doorbell_soak — RUN3-BRIEF §15.2, the experiment that settles whether
// an in-kernel GPU->host system-scope atomic doorbell is viable on gfx1151.
//
// ds4's documented failure (lost arrivals at gate seq 1306/1482) indicts
// hipStreamWriteValue64 stream-memory-op PACKETS. An in-kernel
// __hip_atomic_store is a different mechanism: a store executed by a wave inside
// an already-dispatched kernel, with no separate packet to lose. ds4 has no
// evidence either way about it under sustained load. This probe produces that
// evidence.
//
// Forked in shape from ds4's scripts/hip_host_callback_gate_probe.cpp so the
// per-gate cost is directly comparable to the ordered-callback mechanism.
//
// ACCEPTANCE: zero missed arrivals over 25800 gates. Their failure showed at
// 1306, so anything under ~2000 iterations proves nothing.
//
// Build: hipcc -O3 --offload-arch=gfx1151 hip_atomic_doorbell_soak.cpp -o ...
// Run:   ./hip_atomic_doorbell_soak [gates] [delay_us] [mapped|device] [checkpayload]

#include <hip/hip_runtime.h>

#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>

#define PAYLOAD_WORDS 4096
// Per-iteration deadline. Generous: a miss is a miss, not a slow arrival.
#define DEADLINE_NS (200ull * 1000ull * 1000ull)

static inline uint64_t monotonic_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

// Producer: fill the payload, publish it with a system-scope release fence, then
// ring the doorbell with a system-scope release atomic. This is tbv_ar2.hip's
// signalling shape, not hipStreamWriteValue64's.
__global__ static void doorbell_producer(uint32_t *payload, unsigned long long *flag,
                                         unsigned long long seq) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int stride = gridDim.x * blockDim.x;
    // Integer payload: exactly representable, and immune to the device-side
    // FMA contraction that makes a float expression differ from the host's.
    for (int i = tid; i < PAYLOAD_WORDS; i += stride) {
        payload[i] = (uint32_t)seq ^ (uint32_t)(i * 2654435761u);
    }
    __threadfence_system();
    __syncthreads();
    if (tid == 0) {
        __hip_atomic_store(flag, seq, __ATOMIC_RELEASE, __HIP_MEMORY_SCOPE_SYSTEM);
    }
}

static int check(hipError_t err, const char *what) {
    if (err == hipSuccess) return 1;
    std::fprintf(stderr, "%s: %s\n", what, hipGetErrorString(err));
    return 0;
}

int main(int argc, char **argv) {
    const uint32_t gates    = argc > 1 ? (uint32_t)std::strtoul(argv[1], nullptr, 10) : 25800u;
    const uint32_t delay_us = argc > 2 ? (uint32_t)std::strtoul(argv[2], nullptr, 10) : 57u;
    const std::string alloc = argc > 3 ? argv[3] : "mapped";
    const bool check_payload = argc > 4 ? (std::strcmp(argv[4], "checkpayload") == 0) : false;

    if (gates == 0) { std::fprintf(stderr, "gate count must be nonzero\n"); return 2; }
    const bool mapped = (alloc == "mapped");
    if (!mapped && alloc != "device") {
        std::fprintf(stderr, "allocator must be 'mapped' or 'device'\n"); return 2;
    }

    unsigned long long *flag_host = nullptr;    // what the CPU polls
    unsigned long long *flag_dev  = nullptr;    // what the kernel stores into
    uint32_t *payload_host = nullptr;
    uint32_t *payload_dev  = nullptr;

    // Payload always rides mapped memory: §14.3 says a CPU read of hipMalloc'd
    // memory is write-combining at ~200 MB/s, which would swamp the measurement.
    // The FLAG allocator is the axis under test (ds4 parameterized exactly this
    // and never published which won).
    if (!check(hipHostMalloc(&payload_host, PAYLOAD_WORDS * sizeof(uint32_t),
                             hipHostMallocMapped), "hipHostMalloc payload") ||
        !check(hipHostGetDevicePointer((void **)&payload_dev, payload_host, 0),
               "hipHostGetDevicePointer payload")) return 1;

    if (mapped) {
        if (!check(hipHostMalloc(&flag_host, sizeof(unsigned long long),
                                 hipHostMallocMapped), "hipHostMalloc flag") ||
            !check(hipHostGetDevicePointer((void **)&flag_dev, flag_host, 0),
                   "hipHostGetDevicePointer flag")) return 1;
    } else {
        // Device allocation, polled directly by the host. On this UMA APU that
        // read is write-combining -- the case ds4 suspected mattered.
        if (!check(hipMalloc((void **)&flag_dev, sizeof(unsigned long long)),
                   "hipMalloc flag")) return 1;
        flag_host = flag_dev;
        if (!check(hipMemset(flag_dev, 0, sizeof(unsigned long long)),
                   "hipMemset flag")) return 1;
    }
    if (mapped) *flag_host = 0ull;
    std::memset(payload_host, 0, PAYLOAD_WORDS * sizeof(uint32_t));

    hipStream_t stream;
    if (!check(hipStreamCreate(&stream), "hipStreamCreate")) return 1;
    if (!check(hipDeviceSynchronize(), "warmup sync")) return 1;

    uint64_t arrivals_missed = 0;
    uint64_t first_miss_seq  = 0;
    uint64_t payload_mismatches = 0;
    uint64_t first_mismatch_seq = 0;
    double   max_detect_us = 0.0;
    double   sum_detect_us = 0.0;

    const uint64_t begin = monotonic_ns();
    for (uint32_t i = 0; i < gates; i++) {
        const unsigned long long seq = (unsigned long long)i + 1ull;

        // ONE block: __syncthreads() is only a block-wide barrier, so a
        // multi-block grid can ring the doorbell while sibling blocks are still
        // writing the payload. One block makes the barrier grid-wide.
        hipLaunchKernelGGL(doorbell_producer, dim3(1), dim3(256), 0, stream,
                           payload_dev, flag_dev, seq);
        if (!check(hipGetLastError(), "producer launch")) return 1;

        // Host spins for the doorbell exactly as a progress thread would.
        const uint64_t t0 = monotonic_ns();
        const uint64_t deadline = t0 + DEADLINE_NS;
        bool seen = false;
        for (;;) {
            const unsigned long long v =
                __atomic_load_n((volatile unsigned long long *)flag_host, __ATOMIC_ACQUIRE);
            if (v >= seq) { seen = true; break; }
            if (monotonic_ns() >= deadline) break;
#if defined(__x86_64__)
            __builtin_ia32_pause();
#endif
        }
        const uint64_t t1 = monotonic_ns();

        if (!seen) {
            arrivals_missed++;
            if (first_miss_seq == 0) first_miss_seq = seq;
            // Do not leave the GPU behind: drain before continuing.
            (void)hipStreamSynchronize(stream);
        } else {
            const double detect_us = (double)(t1 - t0) / 1000.0;
            if (detect_us > max_detect_us) max_detect_us = detect_us;
            sum_detect_us += detect_us;

            if (check_payload) {
                // T2's check: does flag arrival imply the payload is visible?
                for (int k = 0; k < PAYLOAD_WORDS; k++) {
                    const uint32_t want = (uint32_t)seq ^ (uint32_t)(k * 2654435761u);
                    if (payload_host[k] != want) {
                        payload_mismatches++;
                        if (first_mismatch_seq == 0) first_mismatch_seq = seq;
                        break;
                    }
                }
            }
        }

        if (delay_us != 0) {
            const uint64_t until = monotonic_ns() + (uint64_t)delay_us * 1000ull;
            while (monotonic_ns() < until) {
#if defined(__x86_64__)
                __builtin_ia32_pause();
#endif
            }
        }
    }
    if (!check(hipStreamSynchronize(stream), "final sync")) return 1;
    const uint64_t finished = monotonic_ns();

    const double total_ms = (double)(finished - begin) / 1.0e6;
    const uint64_t arrived = (uint64_t)gates - arrivals_missed;
    std::printf("gates=%u delay_us=%u flag_allocator=%s check_payload=%d "
                "total_ms=%.3f total_us_per_gate=%.3f "
                "arrivals_missed=%" PRIu64 " first_miss_seq=%" PRIu64 " "
                "mean_detect_us=%.3f max_detect_us=%.3f "
                "payload_mismatches=%" PRIu64 " first_mismatch_seq=%" PRIu64 " "
                "VERDICT=%s\n",
                gates, delay_us, alloc.c_str(), check_payload ? 1 : 0,
                total_ms, total_ms * 1000.0 / (double)gates,
                arrivals_missed, first_miss_seq,
                arrived ? sum_detect_us / (double)arrived : 0.0, max_detect_us,
                payload_mismatches, first_mismatch_seq,
                (arrivals_missed == 0 && payload_mismatches == 0) ? "PASS" : "FAIL");

    (void)hipStreamDestroy(stream);
    if (mapped) (void)hipHostFree(flag_host); else (void)hipFree(flag_dev);
    (void)hipHostFree(payload_host);
    return (arrivals_missed == 0 && payload_mismatches == 0) ? 0 : 1;
}
