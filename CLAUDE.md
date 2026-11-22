# URL Shortener — working agreement

- Spec-driven development with OpenSpec. Read `openspec/AGENTS.md` and `openspec/project.md` before starting any work.
- Start a feature with `/add-url-shortner-feature <number> <description>`. It checks for a clean git tree, creates `feature/<change-id>`, writes specs, asks for plan approval, then implements.
- Delivery order and status: `docs/features/features-v1.md`. Exactly **one** active change in `openspec/changes/` at a time.
- Never write implementation code for a change until its proposal and spec deltas are approved by the user.
- Implement `tasks.md` in order and tick tasks only after verifying them.
- A change is done only when its scenarios pass against the local Docker Compose stack (`make up`, `make test-api`).
- Design references: `docs/hld/hld-v1.md`, `docs/lld/lld-v1.md`. Update them if implementation diverges.
