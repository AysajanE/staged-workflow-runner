# Synthetic Repo Fact Sheet

The synthetic validation pack represents the intended v2 framework behavior.

Current package facts for this synthetic example:

- runtime output root: `.local/automation/responses_runner_v2/runs/`
- primary-generation default model role: `gpt-5.4-pro`
- structural-processing default model role: `gpt-5.4`
- review gates are satisfied only by `review_bundle.json`
- text-first stages may emit `output.structured.json` through a built-in sidecar
- the example pack uses an explicit no-tools profile