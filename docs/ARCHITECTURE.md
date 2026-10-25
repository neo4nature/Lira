# Memory-only architecture

```text
Explicit files / reviewed records / source messages
           | source boundary + version/provenance
           v
Append-only embedding records ----> freshness sidecar
           |                            |
           +------> vector matrix <-----+
           |        semantic retrieval
           +------> word/context retrieval
                             |
                   retrieval observation
                             |
                   independent use receipt

Explicit source messages --> social/work split --> generation callback
                                                --> validation + core state
Explicit evidence + reviewed operations ----------> decision registry
```

Storage lives under the explicitly configured data directory. Event-store, availability and configuration helpers are minimal memory adapters; they do not initialize any other product. The `agents/core` package initializer is empty. No installer, storage guardian, executive loop, model router or agent identity loader is imported.

Context creation accepts an explicit retrieval function. Reconstruction requires explicit records and source IDs and refuses missing sources. Neither action discovers personal histories. The embedding HTTP boundary refuses an unconfigured endpoint and network access is disabled by default.

The source matrix implementation, version freshness logic, chunking, source classification, literal search, retrieval-use chain and consolidation validators come from the existing implementation. The configuration, event-store facade, process-local availability callback, generic reviewed importer and small context interfaces are extracted/new adapters and require independent review.
