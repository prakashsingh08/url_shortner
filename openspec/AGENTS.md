# OpenSpec Instructions

Instructions for AI assistants and humans working in this repo with spec-driven development.

## Layout

```
openspec/
├── project.md              # Project context and conventions
├── specs/                  # CURRENT truth: what is built (one folder per capability)
│   └── <capability>/spec.md
└── changes/                # PROPOSALS: what should change
    ├── <change-id>/
    │   ├── proposal.md     # Why, What Changes, Impact
    │   ├── design.md       # Technical decisions (only when non-trivial)
    │   ├── tasks.md        # Implementation checklist
    │   └── specs/<capability>/spec.md   # Spec deltas
    └── archive/YYYY-MM-DD-<change-id>/  # Completed changes
```

## Workflow (one change at a time)

1. **Propose:** pick the next `Planned` feature in `docs/features/features-v1.md`. Create `changes/<change-id>/` with proposal, tasks, spec deltas (and design if needed). Mark the feature `Proposed`.
2. **Review:** the user approves or requests changes. Do not write implementation code before approval. Mark `Approved`.
3. **Implement:** work through `tasks.md` in order, ticking `- [x]` as each task is finished and verified. Mark `In Progress`.
4. **Verify:** all tests pass and every scenario passes on the local Docker Compose stack.
5. **Archive:** move the folder to `changes/archive/YYYY-MM-DD-<change-id>/`, merge its deltas into `specs/<capability>/spec.md`, and mark the feature `Done`.

## Spec Delta Format

```markdown
## ADDED Requirements
### Requirement: <Name>
The system SHALL <behaviour>.

#### Scenario: <Name>
- **WHEN** <condition>
- **THEN** <outcome>
- **AND** <further outcome>

## MODIFIED Requirements
### Requirement: <Existing name>
<Full updated requirement text, including all scenarios>

## REMOVED Requirements
### Requirement: <Existing name>
**Reason**: ...
**Migration**: ...
```

Rules:
- Every requirement uses SHALL/MUST and has at least one `#### Scenario:` (exactly four `#`).
- MODIFIED requirements repeat the whole requirement, not just the diff.
- Change IDs are kebab-case, verb-led: `add-…`, `update-…`, `remove-…`.

## Validation

With the CLI installed (`npm install -g @fission-ai/openspec`):

```bash
openspec list                       # active changes
openspec show <change-id>
openspec validate <change-id> --strict
openspec archive <change-id> --yes  # after deployment/verification
```
