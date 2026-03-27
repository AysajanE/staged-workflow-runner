Read the attached markdown artifact and raw response JSON from a prior runner execution.

Return only a JSON object that matches the provided schema.
Extract only information that is explicitly supported by the attachments.

Rules:
- If a required string field is not supported by the attachments, use the exact string `"not stated in artifact"`.
- If a required array field has no supported entries, use an empty array.
- Do not invent facts, citations, file names, phases, risks, dependencies, or decisions.