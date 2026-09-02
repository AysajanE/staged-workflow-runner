# Document Evidence Synthesis Example

This small, non-coding example shows how the runner can prepare a high-stakes business
decision brief from attached documents without pretending to implement software or take an
external action.

The corpus is synthetic and includes deliberately conflicting customer-count and cost-savings
claims. The workflow has no tools and requires review before each downstream stage. Its
`runtime_input_bindings.example.json` records which named input is visible to which stage.
Static input manifests are deliberately empty, so using the binding file does not attach the
same content twice.

The workflow declares the `reviewed` assurance profile: it is an important deliverable with
review gates, but it does not claim the `critical` data-handling posture (confidential
sensitivity, repository-file evidence).

Citation types used by the prompts are:

- `[workspace_file:<path>]` for attached source documents;
- `[stage_artifact:<stage_id>]` for an approved prior-stage artifact;
- `[operator_input:<binding_id>]` for the decision question supplied by the operator.

Run the offline contract test (no API credentials or network required):

```bash
python -m unittest automation.tests.test_responses_runner_v2_evidence_synthesis_example
```

The example intentionally stops at a written recommendation. It does not authorize a purchase,
message, contract, deployment, or other real-world action.
