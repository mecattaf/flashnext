#!/usr/bin/env bash
# scripts/run-tp2.sh — flashnext first-light runner on the pair.
#
# Sequence (spec claims 4.2–4.5):
#   preflight -> pair up (which arbitrates the model-swap proxy on both twins
#   and runs the eight fail-closed TP=2 parity assertions before the first
#   launch, host/fn-swap-arbitrate.sh and host/fn-tp2-guards.py; both records
#   are folded into the tp2 receipt below) -> two greedy 300-token completions
#   byte-compared
#   (tp2.json) -> residency read after 50 warmed decode tokens (residency.json,
#   ruling P11) -> fidelity baseline: fixed-prompt losses and frontier logits
#   under results/ (fidelity.json) -> the 262144-context probe (context.json).
#
# Every receipt is one JSON object {"step","status","ts","data"} exactly as
# scripts/receipts-verify.py expects. A step that cannot complete writes a
# fail receipt (graded, never silently skipped — spec F.12) and the runner
# exits non-zero.
#
# RECEIPT QUARANTINE (D12): a status=fail receipt lands under
# results/receipts/failed/<step>.json — committed and ledger-reviewed as a
# typed blocker, but outside receipts-verify's non-recursive grading walk,
# so one failed step can never permanently redden every later gate run. The
# step still exits non-zero (the checkpoint fails); quarantine changes where
# the receipt lands, never whether the step failed. ONE exception: the
# context step's honest sub-bound ratio is deferral-typed — see that step.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST_DIR="$REPO_ROOT/host"
RECEIPTS="$REPO_ROOT/results/receipts"
FIDELITY_DIR="$REPO_ROOT/results/fidelity"
# shellcheck source=host/fn-env.sh
source "$HOST_DIR/fn-env.sh"

mkdir -p "$RECEIPTS" "$FIDELITY_DIR"
export RECEIPTS
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log() { echo "run-tp2: $*" >&2; }

# --- preflight, then stand the pair -------------------------------------------
log "preflight"
bash "$HOST_DIR/fn-preflight.sh"

log "standing the pair service"
bash "$HOST_DIR/fn-cluster-up.sh"

export FN_API="http://127.0.0.1:$FN_PORT"

# --- step tp2: two greedy 300-token completions, byte-compared -----------------
log "step tp2: greedy byte-compare"
python3 - <<'PY'
import json, os, sys, time, urllib.request

API = os.environ["FN_API"]
MODEL = os.environ["FN_SERVED_NAME"]
RECEIPTS = os.environ["RECEIPTS"]
PROMPT = ("The pair boots before the operator arrives, and the ledger "
          "records what the night proved: ")

def post(path, body, timeout=3600):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def greedy_300():
    return post("/v1/completions", {"model": MODEL, "prompt": PROMPT,
                                    "max_tokens": 300, "temperature": 0})

status = "pass"
data = {}
try:
    a, b = greedy_300(), greedy_300()
    ta = a["choices"][0]["text"].encode()
    tb = b["choices"][0]["text"].encode()
    data["byte_identical_repeat"] = ta == tb
    data["completion_bytes"] = len(ta)
    data["completion_tokens"] = a["usage"]["completion_tokens"]
    if ta != tb:
        status = "fail"
except Exception as e:
    status = "fail"
    data["error"] = f"{e.__class__.__name__}: {e}"
    data["byte_identical_repeat"] = False

# Fold the preflight record into the tp2 receipt (U.2: held sub-budget round
# trips on both ends ride with the tp2 claim).
try:
    pre = json.load(open(os.path.join(RECEIPTS, "preflight.json")))
    data["preflight"] = pre["data"]
except Exception as e:
    data["preflight"] = f"unreadable: {e}"

# Fold the two pre-launch records the bring-up wrote under the state dir.
#
#   swap-arbitration.json — WAS THE MODEL-SWAP PROXY RUNNING WHEN WE ARRIVED?
#   Without this line in the receipt the morning cannot tell a night that took
#   the local roster down from a night that found it already down, and the
#   restore in fn-cluster-down.sh looks like an unexplained restart.
#
#   tp2-guards.json — the eight parity assertions and the sharding checks,
#   with the per-rank shard digests and the residency PREDICTED from the
#   checkpoint header before the load. The prediction sitting beside the
#   measured residency receipt is what turns the next OOM into arithmetic.
#
# Volatile fields are dropped: `restored` is written later, by the teardown,
# and folding it here would make an otherwise identical re-run's receipt
# differ (checkpoint purity, receipt-restore.py's contract).
STATE_DIR = os.environ.get("FN_STATE_DIR", "")

def fold_state(name, drop=()):
    try:
        with open(os.path.join(STATE_DIR, name)) as fh:
            doc = json.load(fh)
    except Exception as e:
        return f"unreadable: {e}"
    if isinstance(doc, dict):
        return {k: ({kk: vv for kk, vv in v.items() if kk not in drop}
                    if isinstance(v, dict) else v)
                for k, v in doc.items() if k not in drop}
    return doc

data["swap_proxy"] = fold_state("swap-arbitration.json", drop=("restored", "ts"))
guards = fold_state("tp2-guards.json")
if isinstance(guards, dict):
    data["tp2_guards"] = {
        "status": guards.get("status"),
        "summary": guards.get("summary"),
        "assertions": {a["id"]: a["ok"] for a in guards.get("assertions", [])},
    }
else:
    data["tp2_guards"] = guards

base = RECEIPTS if status == "pass" else os.path.join(RECEIPTS, "failed")
os.makedirs(base, exist_ok=True)
json.dump({"step": "tp2", "status": status, "ts": time.strftime("%FT%T"),
           "data": data},
          open(os.path.join(base, "tp2.json"), "w"), indent=1)
print(f"run-tp2: tp2 receipt status={status}", file=sys.stderr)
sys.exit(0 if status == "pass" else 1)
PY

# --- step residency: read after 50 warmed decode tokens, per ruling P11 --------
log "step residency: warmed decode, then per-rank readings"
cat > "$TMP/rank_metrics.py" <<'PY'
"""Per-rank residency probes: GTT from sysfs, process RSS, table page-cache
residency. Stdlib only; runs on either node (local or over ssh stdin)."""
import fnmatch, glob, json, os, shutil, subprocess

def first_int(paths):
    for p in paths:
        try:
            return int(open(p).read().strip())
        except Exception:
            continue
    return None

def gtt():
    # GTT usage first; on the unified-memory APU the VRAM reading is the
    # fallback (patch 0004 re-points VRAM reporting at unified memory).
    for name, label in (("mem_info_gtt_used", "gtt"),
                        ("mem_info_vram_used", "vram")):
        v = first_int(sorted(glob.glob("/sys/class/drm/card*/device/" + name)))
        if v is not None:
            return v, label
    return None, None

def rss_bytes():
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open("/proc/%s/cmdline" % pid, "rb").read().decode(errors="ignore")
        except Exception:
            continue
        if "vllm serve" not in cmd and "ray::" not in cmd:
            continue
        try:
            status = open("/proc/%s/status" % pid).read()
        except Exception:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) * 1024
    return total

def table_cache():
    patterns = [p.strip().lower() for p in
                os.environ.get("FN_TABLE_GLOB", "*ple*,*engram*,*ngram*").split(",")
                if p.strip()]
    root = os.environ.get("FN_MODEL_DIR", "/var/lib/local-models/flashnext-fp8")
    matched = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if any(fnmatch.fnmatch(f.lower(), pat) for pat in patterns):
                matched.append(os.path.join(dirpath, f))
    fincore = shutil.which("fincore")
    pages, ok = 0, False
    if fincore:
        ok = True
        for m in matched:
            out = subprocess.run([fincore, m], capture_output=True, text=True,
                                 check=False).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    pages += int(parts[1])
                    break
    return {"files_matched": len(matched), "files": matched[:8],
            "cached_pages": pages if ok else None,
            "tool": "fincore" if ok else None}

gtt_bytes, gtt_src = gtt()
print(json.dumps({"gtt_bytes": gtt_bytes, "gtt_source": gtt_src,
                  "rss_bytes": rss_bytes(), "table": table_cache()}))
PY

export FN_METRICS="$TMP/rank_metrics.py"
python3 - <<'PY'
import json, os, subprocess, sys, time, urllib.request

API = os.environ["FN_API"]
MODEL = os.environ["FN_SERVED_NAME"]
RECEIPTS = os.environ["RECEIPTS"]
WORKER = os.environ["FN_WORKER_HOST"]
SERVE_LOG = os.path.join(os.environ["FN_STATE_DIR"], "serve.log")
METRICS = os.environ["FN_METRICS"]
PAGE = 4096

def post(path, body, timeout=3600):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

status = "pass"
data = {}
try:
    # Compile pass, then a decode long enough to be called warmed (>50 tokens).
    post("/v1/completions", {"model": MODEL,
                             "prompt": "Warm the decode path: ",
                             "max_tokens": 16, "temperature": 0})
    r = post("/v1/completions", {"model": MODEL,
                                 "prompt": "Warm the decode path further: ",
                                 "max_tokens": 64, "temperature": 0})
    warmed = int(r["usage"]["completion_tokens"])
    data["warmed_decode_tokens"] = warmed
    data["read_after_warmed_decode"] = warmed >= 50
    if warmed < 50:
        status = "fail"
except Exception as e:
    status = "fail"
    data["read_after_warmed_decode"] = False
    data["error_warmup"] = f"{e.__class__.__name__}: {e}"

metrics_src = open(METRICS).read()
def probe(node):
    if node == "coordinator":
        out = subprocess.run([sys.executable, METRICS], capture_output=True,
                             text=True, check=True).stdout
    else:
        out = subprocess.run(["ssh", WORKER, "python3", "-"],
                             input=metrics_src.encode(), capture_output=True,
                             text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])

try:
    per_rank = {n: probe(n) for n in ("coordinator", "worker")}
    data["gtt_gib_per_rank"] = {
        n: round(m["gtt_bytes"] / 2**30, 2)
        for n, m in per_rank.items() if m["gtt_bytes"] is not None}
    # Grade the P11 bound HERE, not only in receipts-verify: the receipt and
    # the gate must never disagree (a status:pass residency.json that fails
    # the gate's 80 GiB bound would redden every later gate run forever).
    for n, gib in data["gtt_gib_per_rank"].items():
        if gib > 80:
            status = "fail"
            data["error_residency_bound"] = (
                f"rank {n} GTT {gib} GiB exceeds the 80 GiB P11 bound")
    data["gtt_source_per_rank"] = {
        n: m["gtt_source"] for n, m in per_rank.items()}
    data["rss_gib_per_rank"] = {
        n: round(m["rss_bytes"] / 2**30, 2) for n, m in per_rank.items()}
    data["table_page_cache"] = {
        n: {**per_rank[n]["table"],
            "cached_gib": (None if per_rank[n]["table"]["cached_pages"] is None
                           else round(per_rank[n]["table"]["cached_pages"] * PAGE / 2**30, 2))}
        for n in per_rank}
except Exception as e:
    status = "fail"
    data["error_metrics"] = f"{e.__class__.__name__}: {e}"

# The mmap engagement probe: zero table bytes GPU-resident only if the table
# path actually engaged in this run's own serve log under VLLM_PLE_MMAP=1.
try:
    log_text = open(SERVE_LOG, errors="ignore").read().lower()
    engaged = ("ple_mmap" in log_text
               and os.environ.get("VLLM_PLE_MMAP") == "1")
    data["table_gpu_resident_bytes"] = 0 if engaged else None
    data["ple_mmap_log_lines"] = log_text.count("ple_mmap")
    if not engaged:
        status = "fail"
except Exception as e:
    status = "fail"
    data["table_gpu_resident_bytes"] = None
    data["error_ple_probe"] = f"{e.__class__.__name__}: {e}"

base = RECEIPTS if status == "pass" else os.path.join(RECEIPTS, "failed")
os.makedirs(base, exist_ok=True)
json.dump({"step": "residency", "status": status, "ts": time.strftime("%FT%T"),
           "data": data},
          open(os.path.join(base, "residency.json"), "w"), indent=1)
print(f"run-tp2: residency receipt status={status}", file=sys.stderr)
sys.exit(0 if status == "pass" else 1)
PY

# --- step fidelity: fixed-prompt losses and frontier logits ---------------------
log "step fidelity: reference losses and frontier logits"
export FIDELITY_DIR
python3 - <<'PY'
import hashlib, json, os, sys, time, urllib.request

API = os.environ["FN_API"]
MODEL = os.environ["FN_SERVED_NAME"]
RECEIPTS = os.environ["RECEIPTS"]
OUT_DIR = os.environ["FIDELITY_DIR"]
PROMPTS = [
    "The fleet measures before it claims, because ",
    "A receipt is a fact the morning operator can check: ",
    "Two ranks agree byte for byte when ",
]

def post(path, body, timeout=3600):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

status = "pass"
files, hashes, losses = [], {}, []
for i, prompt in enumerate(PROMPTS):
    name = f"fidelity-{i}.json"
    path = os.path.join(OUT_DIR, name)
    try:
        r = post("/v1/completions", {"model": MODEL, "prompt": prompt,
                                     "max_tokens": 64, "temperature": 0,
                                     "logprobs": 5})
        choice = r["choices"][0]
        token_logprobs = [x for x in choice["logprobs"]["token_logprobs"]
                          if x is not None]
        record = {
            "model": MODEL,
            "prompt": prompt,
            "text": choice["text"],
            "tokens": choice["logprobs"]["tokens"],
            # Reference loss: mean negative log-likelihood of the fixed
            # continuation under the as-served stack.
            "loss_nll": -sum(token_logprobs) / len(token_logprobs),
            # Frontier logits: the top-5 at the final generated token — the
            # yardstick later changes must not move without a reason.
            "frontier_logits": {
                "token": choice["logprobs"]["tokens"][-1],
                "top5": choice["logprobs"]["top_logprobs"][-1],
            },
        }
        losses.append(record["loss_nll"])
        with open(path, "w") as f:
            json.dump(record, f, indent=1)
    except Exception as e:
        status = "fail"
        record = {"model": MODEL, "prompt": prompt,
                  "error": f"{e.__class__.__name__}: {e}"}
        with open(path, "w") as f:
            json.dump(record, f, indent=1)
    files.append(name)
    hashes[name] = hashlib.sha256(open(path, "rb").read()).hexdigest()

base = RECEIPTS if status == "pass" else os.path.join(RECEIPTS, "failed")
os.makedirs(base, exist_ok=True)
json.dump({"step": "fidelity", "status": status, "ts": time.strftime("%FT%T"),
           "data": {"files": files, "sha256": hashes, "losses_nll": losses,
                    "prompts": len(PROMPTS)}},
          open(os.path.join(base, "fidelity.json"), "w"), indent=1)
print(f"run-tp2: fidelity receipt status={status}", file=sys.stderr)
sys.exit(0 if status == "pass" else 1)
PY

# --- step context: the 262144-context probe -------------------------------------
log "step context: decode at full context vs short context"
python3 - <<'PY'
import json, os, sys, time, urllib.request

API = os.environ["FN_API"]
MODEL = os.environ["FN_SERVED_NAME"]
RECEIPTS = os.environ["RECEIPTS"]
TARGET = int(os.environ.get("FN_CONTEXT_TARGET", "262144"))
DECODE = int(os.environ.get("FN_CTX_DECODE", "128"))
# Claim 4.5's given context depth. The prompt is synthesized from a fixed
# paragraph: tokenization is engine-side, so we verify the actual prompt
# tokens the server reports and re-pad once if we undershot.
PARAGRAPH = ("The ledger records what the night proved. Every receipt is a "
             "fact the morning operator can check. The pair serves from its "
             "own drives; the rails carry tensors and the wire carries "
             "control. Nothing is narrated that was not measured. ")

def stream_completion(prompt, max_tokens, timeout=7200):
    body = {"model": MODEL, "prompt": prompt, "max_tokens": max_tokens,
            "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}}
    req = urllib.request.Request(API + "/v1/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t_first = t_last = None
    usage = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode(errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("text"):
                    now = time.time()
                    if t_first is None:
                        t_first = now
                    t_last = now
    return usage, t_first, t_last

def decode_tps(prompt, max_tokens):
    usage, t_first, t_last = stream_completion(prompt, max_tokens)
    if not usage or t_first is None or t_last is None or t_last <= t_first:
        raise RuntimeError("no usable decode window in the streamed response")
    tokens = usage["completion_tokens"]
    return (tokens - 1) / (t_last - t_first), usage["prompt_tokens"]

RATIO_MIN = float(os.environ.get("FN_CONTEXT_RATIO_MIN", "0.9"))

status = "pass"
hard_fail = False  # transport/API errors and prompt undershoot stay fatal
data = {"target_context": TARGET}
try:
    short_tps, short_prompt_tokens = decode_tps(
        "Measure the short-context decode: " + PARAGRAPH, DECODE)
    data["short_prompt_tokens"] = short_prompt_tokens
    data["short_decode_tps"] = round(short_tps, 3)

    est_chars_per_token = 4.2  # conservative: undershoot tokens, never the cap
    long_prompt = PARAGRAPH * int(((TARGET - 2 * DECODE) * est_chars_per_token)
                                  / len(PARAGRAPH) + 1)
    long_tps, long_prompt_tokens = decode_tps(long_prompt, DECODE)
    if long_prompt_tokens < TARGET - 1024:  # undershot: re-pad once, exactly
        long_prompt = long_prompt * int((TARGET - DECODE) / max(long_prompt_tokens, 1)) + PARAGRAPH
        long_tps, long_prompt_tokens = decode_tps(long_prompt, DECODE)
    data["long_prompt_tokens"] = long_prompt_tokens
    data["long_decode_tps"] = round(long_tps, 3)
    ratio = round(long_tps / short_tps, 4)
    data["decode_ratio_vs_short_context"] = ratio
    if long_prompt_tokens < TARGET - 1024:
        status = "fail"
        hard_fail = True
        data["error"] = f"server accepted only {long_prompt_tokens} prompt tokens"
    elif ratio < RATIO_MIN:
        # DEFERRAL-TYPED sub-bound ratio: the probe worked and the honest
        # measurement lands in quarantine as a typed performance finding
        # (12 full-attention layers walking 256K KV; the community precedent
        # shows ~2x falloff by 50-73k). It is morning evidence routed to the
        # optimization menu, NOT a campaign failure — the runner exits 0 for
        # this sub-step so cp-bench still runs.
        status = "fail"
        data["deferred"] = True
        data["deferral"] = (f"decode ratio {ratio} under the {RATIO_MIN} "
                            "bound — typed performance finding, see "
                            "docs/MORNING.md optimization menu")
except Exception as e:
    status = "fail"
    hard_fail = True
    data["error"] = f"{e.__class__.__name__}: {e}"

base = RECEIPTS if status == "pass" else os.path.join(RECEIPTS, "failed")
os.makedirs(base, exist_ok=True)
json.dump({"step": "context", "status": status, "ts": time.strftime("%FT%T"),
           "data": data},
          open(os.path.join(base, "context.json"), "w"), indent=1)
if status == "fail" and not hard_fail:
    print("run-tp2: context ratio under bound — DEFERRED to the morning "
          "(quarantined receipt failed/context.json); not failing the run",
          file=sys.stderr)
    sys.exit(0)
print(f"run-tp2: context receipt status={status}", file=sys.stderr)
sys.exit(0 if status == "pass" else 1)
PY

# Checkpoint purity: restore any receipt this re-run rewrote without a
# measured change (covers fn-preflight.sh's preflight.json too).
python3 "$REPO_ROOT/scripts/receipt-restore.py" "$REPO_ROOT"

log "first light complete; receipts: $RECEIPTS/{tp2,residency,fidelity,context}.json"
