# SigLIP2 + Qwen 4B image cascade

Compare three image-presence methods on the same original 37 test posters: SigLIP2, Qwen3.5-4B and a sequential SigLIP2 → selective Qwen cascade. This is inference only; no training or installation.

The saved paired development predictions select a policy before test labels are loaded. Candidate margins 0.05, 0.10, 0.20, 0.30 and 0.50 are measured from each SigLIP label threshold. Each candidate routes either only the instrument label or all labels within the margin; one Qwen call covers all routed labels. An unanswered Qwen label keeps SigLIP's decision. Select development macro F1 first, then accuracy, then fewer Qwen calls; candidates must not fall below the SigLIP development accuracy or macro F1. The always-SigLIP policy is a candidate, so the selected policy may make zero Qwen calls. No gold or Qwen result is used to route a test image.

On RunPod restore the three files plus the existing complementarity helper and run:

```bash
bash run_image_cascade.sh run --resume
bash run_image_cascade.sh show-results
```

The source is `runs/qwen4b-image-compare-v1`, output is `runs/siglip-qwen-cascade-v1`. No saved result or source model is overwritten. Both models are resident on the same GPU; there is a conservative resource precheck, but it does not guarantee loading both will fit. Nothing is automatically downloaded, installed, deleted or offloaded. A failure leaves per-image request files for `--resume` when environment and protocol are unchanged.

`dev_candidates.csv` and `selection_frozen.json` show the development decision. `comparison.csv` gives accuracy, conservative macro F1, coverage, call rate and actual request-level mean/median/p95; `per_label.csv` contains TP/FP/FN/TN and unanswered counts; `timing_raw.csv` contains each timed request; `predictions.json` and `requests/` contain prediction evidence. Unknown gold cells are excluded, while unanswered known labels count as incorrect; positive unanswered counts toward conservative F1 recall. Scores use the first repeat and latency uses all repeats. Resumed timing sessions are reported.

The cascade is executed and timed separately from either standalone model; SigLIP, routing and any actual Qwen invocation are included in its wall time. Image preprocessing, GPU synchronization, Qwen output generation and parsing are included. Loading, warmup, hashing, checkpoint/model files, network, DB, OCR and output writes are excluded. The fixed order of execution and the two models being resident can affect results. Compare only with the same precision, GPU and request conditions; no pipeline latency or production throughput is claimed.

The prior test images have been repeatedly inspected and used in earlier experiments, and the development policy search is small and exploratory. These results are regression evidence, not an independent estimate. P069/P130 labels are unchanged; review them separately, document any revisions, and evaluate final decisions on a new human-reviewed independent dataset.
