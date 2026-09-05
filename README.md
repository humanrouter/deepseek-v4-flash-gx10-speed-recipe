# DeepSeek V4 Flash on two ASUS GX10s: tested speed profile

A reproducible tuning layer for [MiaAI Lab's two-node DSpark recipe](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark). Same model weights and number precision before and after tuning. This is a settings recipe plus an upstream patch, not a new model or inference engine.

## What improved

Single-request tests on two 128 GB ASUS GX10 / NVIDIA GB10 machines, September 4, 2026:

| Test | Original | Tuned | Change |
|---|---:|---:|---:|
| Read about 8K input tokens | 1,466.80 tokens/s | 1,886.49 tokens/s | +28.61% |
| Read about 32K input tokens | 1,429.52 tokens/s | 1,867.14 tokens/s | +30.61% |
| Generate code | 55.91 tokens/s | 55.91 tokens/s | Flat |
| Generate prose | 39.58 tokens/s | 40.55 tokens/s | +2.45% |

The target was +10% in both prefill and decoding. **The decode target was not met.** This is the best measured combination from five tested configurations, selected mainly for prefill. These are workload-specific results, not a promise for every prompt or installation.

Candidate figures are medians of three cold requests after one warmup per cell. The comparison uses the faster median from the initial baseline and a five-repeat restored baseline, separately for each cell. Requests used unique cache salts; cached or overlapping requests were rejected. Prefill is input tokens divided by time to first output. Decode is `(output tokens - 1) / (last output time - first output time)`, with a 768-token output budget. Long-context prompts were repeated reference text; code and prose prompts were different workloads. This does not measure concurrent-user throughput or the full one-million-token context.

## Differences from latest MiaAI

Compared with upstream commit [`f5665e8`](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark/tree/f5665e8bcde8304654c77a7d58069fdd677198f9), checked September 4, 2026:

| Setting | MiaAI example/default | This profile |
|---|---|---|
| Long prefill chunk threshold | 1024 | 2048 |
| Draft tokens (`MTP_NUM_TOKENS`) | 6 | 5 |
| Block-k patch | Available, off | On |
| RoCE HCAs | Machine-specific placeholder; dual links documented | Both links explicitly selected on each node |
| B12X blocks per SM | 0 (automatic) | 2 |
| Memory utilization | 0.835 | 0.835 |

MiaAI already includes support for the five-token option. We did not invent that patch. This recipe pins the older **tested base `c444d70` plus the exact seven-file block-k patch**. It does not silently upgrade all other changes on upstream main. Future updates need another test.

## Exact tested setup

- Two ASUS GX10s with 128 GB unified memory each, tensor parallel size 2, two working RoCE links between them.
- Model: `drowzeys/keys-DeepSeekV4Flash-Vision-EXP-ablit`, revision `48095b3452a17f3e3ae8f77892399389c45de9e1`. The upstream prepare script resolves this from `ABLITERATED=1`; do not manually override `DSPARK_MODEL`.
- Image pinned in `profile.json`: DSpark GX10 0.1.1, immutable digest `a8394849…ac9d8`; vLLM build `0.25.2.dev0+g752a3a504.d20260714`.
- Context 1,048,576; max sequences 6; max batched tokens 8192; in-flight prefills 2; thinking off; B12X on; `nvfp4_ds_mla` KV cache. No quantization or model change between baseline and tuned tests.
- The head had an existing 2100 MHz GPU clock cap (about 2093 MHz under load); the worker ran around 2411 MHz. The experiment did not change either clock. We do not set clock caps in the installer.
- Tested HCA names were `rocep1s0f1,roceP2p1s0f1`. Interface names can differ. Use yours, not those names blindly.

## Apply to your machines

1. Follow the **pinned MiaAI version's** prerequisites for Docker, NVIDIA drivers/toolkit, SSH from head to worker, model access and cache preparation, and the two RoCE links. The tested model is gated; obtain access and supply your own Hugging Face credentials through the documented login/environment flow. This repository contains no model weights or credentials.
2. On **both nodes**, create the pinned recipe checkout and obtain this tuning repository:

   ```sh
   git clone https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark.git deepseek-v4-flash-2x
   cd deepseek-v4-flash-2x
   git checkout --detach c444d7032957f5a5437261d5366fd06b27a01760
   cp .env.dspark.example .env.dspark
   chmod 600 .env.dspark
   cd ..
   git clone https://github.com/humanrouter/deepseek-v4-flash-gx10-speed-recipe.git
   ```

3. Configure `.env.dspark` using the pinned MiaAI guide: your worker SSH host, head/worker fabric IPs, matching `WORKER_SCRIPT_DIR`, cache paths, and network devices. Check `ibdev2netdev` on each node. Both selected links must be up and configured on both machines. Keep TCP/bootstrap interfaces on your working first rail; use MiaAI's GID auto-detection unless your network requires otherwise.
4. From the tuning repo, check then apply the profile on each node. Replace the HCA names below with the names you verified. The script edits only the source patch and named profile settings; it does not start or stop anything.

   ```sh
   python3 apply-profile.py ../deepseek-v4-flash-2x \
     --head-hcas rocep1s0f1,roceP2p1s0f1 \
     --worker-hcas rocep1s0f1,roceP2p1s0f1
   # Repeat with --apply when the checks pass.
   ```

   The head's environment is authoritative; the MiaAI launcher syncs the worker. Configure the correct worker directory before starting. The script saves a private `.env.dspark.before-speed-*` backup locally. Do not publish it.
5. Complete the pinned MiaAI model-cache preparation for the selected revision, then run its CPU checks on both nodes:

   ```sh
   cd ../deepseek-v4-flash-2x
   python3 scripts/test-dspark-block-k.py
   bash scripts/ci-validate.sh
   ```

6. If replacing a running service, first drain requests and stop it with **its original** stop procedure. Verify neither GPU still has a model process. From the head, launch this recipe:

   ```sh
   ./start-deepseek-v4-flash-dspark.sh --port 8011
   curl --fail http://localhost:8011/v1/models
   ```

   The served model name remains `deepseek-v4-flash-vision-exp`. Verify a real chat completion, both container health states and the actual five-token setting before use. The upstream start script performs the block-k compatibility preflight and applies the patch inside each container.

For a boot service, point its start command at this permanent checkout and include `--port 8011`. Keep its working directory and worker path consistent. This repository does not install system services or network configuration for you.

## Repeat the speed tests

Run on the head with the server on port 8011, and keep all other clients idle:

```sh
python3 benchmark.py --root ./bench-original --label original --repeats 3
# After a separately managed switch to this profile:
python3 benchmark.py --root ./bench-tuned --label tuned --repeats 3
```

The helper uses only the Python standard library. It stores full request outputs and timing/counter data under each result directory. It creates four speed cells plus arithmetic, JSON and tool-call checks; uses a unique cache salt for each request; and refuses incomplete, cached or overlapping samples. A `COMPLETE` result means measurement completed; inspect each `quality_pass` separately. This helper does not manage service switches, clocks, recovery or stability monitoring. Do not run it against a busy endpoint.

## Quality and stability limits

The broader combined-profile canaries passed 27/30 checks. All three failures were a code-trace prompt that asked for JSON only but got narration and hit its 64-token limit. The restored baseline failed the same check on all five attempts and also missed one interval test (34/40 overall). We saw no newly failing check in the candidate; **this does not prove broad quality equivalence**. Free-form throughput answers were not scored for correctness, and some ended at the output budget.

No new GPU Xid occurred in the combined test. Minimum available memory was about 6.26 GiB on the head and 7.11 GiB on the worker. These are observations from this workload, not guarantees for other workloads. Keep a working rollback and remeasure on your own prompts.

To undo the profile, stop the pair first, restore the saved environment on each node, and use a clean checkout of the original pinned base (or reverse this exact patch if no other edits were made). Start using the original profile and verify a real completion. Avoid running old and new model services together.

## Credits and licenses

Serving recipe and block-k work: [MiaAI Lab and contributors](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark). vLLM and its contributors provide the validator fixture embedded in the patch. This repository packages tested tuning settings and reproduction steps; it does not claim authorship of the upstream work.

New helper/documentation: MIT. Included MiaAI portions retain their MIT notice in `licenses/MiaAI-MIT.txt`. The embedded vLLM fixture retains its Apache-2.0 header; see `licenses/vLLM-Apache-2.0.txt`. Model weights and the container retain their own licenses and access terms.
