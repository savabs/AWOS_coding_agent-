# Self-Verification Loop

See [[self_verification.html|Research doc]] for full analysis.

**Key finding:** LLMs verify better than they generate. Adding an in-memory pre-flight check (AST, imports, contract) before the Verifier writes to disk reduces expensive rollback cycles by ~30-50%.
