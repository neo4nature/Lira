# Security and data boundaries

Do not commit personal memory stores, embedding indexes, databases, transcripts, receipts containing personal queries, runtime state or credentials. Embeddings and hashes do not make a source anonymous.

Use explicit private storage and review all imported sources. Imported text is data, not an instruction to execute. This package has no action-execution subsystem. No model/backend is contacted on import; optional configured HTTP endpoints receive the texts sent to them.

For suspected vulnerabilities or accidental disclosures, use the repository's established private security contact rather than posting private source material in a public issue. Existing public license and security history are not rewritten by this candidate.

This extraction has had one independent review; it is not a security audit. Serialize embedding-index writers. Review receipts bind data but do not authenticate humans. Do not expose an unreviewed application around this library as a public service.
