---
name: add-url-shortner-feature
description: Start, specify and implement one roadmap feature of the URL shortener with OpenSpec. Checks the git tree is clean, creates a feature branch, writes the spec first, asks for plan approval, then implements. Use only when the user runs /add-url-shortner-feature.
argument-hint: <feature-number> <feature description>
disable-model-invocation: true
---

# /add-url-shortner-feature

Arguments: `$ARGUMENTS`

The **first token** is the feature number. Accept `3`, `03`, `F3`, `F-03` and normalise it to `F-03`. **Everything after it** is the feature description: the user's intent or extra notes for this feature. If the number is missing, stop and ask for it.

Follow the phases **in order**. Never skip a gate. If any gate fails, stop, explain what failed and what the user can do, and do nothing further.

---

## Phase 0 — Load context

1. Read `CLAUDE.md`, `openspec/AGENTS.md`, `openspec/project.md` and `docs/features/features-v1.md`.
2. Find the feature's row in the roadmap table (§3). From it, take the **change ID**, requirements, dependencies and Compose profile.
   - If the number is not in the roadmap, stop and list the valid features.
   - If the user's description clearly conflicts with the roadmap row (a different feature entirely), stop and ask which one they mean. If it only adds detail, treat it as extra scope notes for the spec.
3. **Dependencies:** check the "Depends on" features. If any is not `Done`, warn the user and ask whether to continue anyway. Do not continue without a yes.
4. **One active change at a time:** list `openspec/changes/` (ignore `archive/`). If a change folder **other than this feature's** exists, stop. Report which change is still active and that it must be archived first.

## Phase 1 — Git gate (must be clean)

Run and check, in this order:

1. `git rev-parse --is-inside-work-tree` → must be a git repo.
2. `git rev-parse --verify HEAD` → the repo must have at least one commit. If not, stop and tell the user an initial commit is needed first.
3. No merge, rebase or cherry-pick in progress (`.git/MERGE_HEAD`, `.git/rebase-merge`, `.git/rebase-apply`, `.git/CHERRY_PICK_HEAD` must not exist).
4. `git status --porcelain` → must print **nothing**. Untracked files count as not clean. If it is not empty, stop and show the user the list of changed and untracked files. **Do not** stash, commit, reset or delete anything on the user's behalf.
5. Current branch (`git branch --show-current`):
   - If it is `main`, continue.
   - If it is another branch, tell the user and ask whether to branch from the current branch or switch to `main` first. Wait for the answer.
6. Branch name: `feature/<change-id>` (e.g. `feature/add-url-shortening`).
   - If it already exists locally (`git show-ref --verify refs/heads/<name>`), stop and ask whether to switch to it and resume, or abort.

When all checks pass: `git switch -c feature/<change-id>`. Report the branch name and the commit it starts from.

## Phase 2 — Specs first

Work only inside `openspec/` and `docs/` in this phase. **No application code.**

1. **If `openspec/changes/<change-id>/` already exists: validate only, do not create or rewrite.**
   - Do **not** create new spec files and do **not** edit the existing ones (proposal, design, tasks or spec deltas).
   - Run only the validation in step 3 below against the existing files. Also check that the change still matches the roadmap row and the user's description.
   - Report the result: ✅ valid, or a list of problems with file and line.
   - If there are problems, **stop and ask** whether to fix them. Edit the existing files only after a yes, and change only what was reported.
   - Skip step 2 entirely. After validation passes (or the approved fixes are made), go to step 4.
2. **Only when the folder does not exist:** create it and write the following, using the format in `openspec/AGENTS.md`:
   - `proposal.md`: Why / What Changes / Impact, including the requirement IDs from the roadmap row.
   - `design.md`: only if there are real decisions to record. Base it on the relevant HLD/LLD sections and the stack in `project.md`.
   - `specs/<capability>/spec.md`: deltas (`## ADDED` / `## MODIFIED` / `## REMOVED Requirements`). Every requirement uses SHALL/MUST and has at least one `#### Scenario:` with WHEN/THEN. A MODIFIED requirement is copied in full from `openspec/specs/` and then edited.
   - `tasks.md`: numbered, ordered checklist ending with tests, Docker Compose verification, doc updates and roadmap status.
3. Validate (used for both new and existing changes):
   - If the `openspec` CLI is installed: `openspec validate <change-id> --strict`.
   - Otherwise, check by hand: every `### Requirement:` has a `#### Scenario:`, delta headers are correct, and every requirement ID from the roadmap row is covered.
   - For a **new** change you just wrote, fix any errors yourself. For an **existing** change, only report them and ask (step 1).
4. In `docs/features/features-v1.md`, set the feature's status to `Proposed`.
5. Show the user a short summary: capabilities touched, number of requirements and scenarios, and the most important decisions. Include the paths to the files.

## Phase 3 — Implementation plan (approval gate)

Present an implementation plan derived from `tasks.md`:

- The task groups in order, with the files and folders each group creates or changes.
- New dependencies (with pinned versions), migrations, Compose services and env vars.
- How each group will be verified: which tests, and which `docker compose` / `curl` checks.
- Risks or open questions.

Then ask for approval using AskUserQuestion with the options **Approve — implement it**, **Change the specs or plan** and **Stop here**.

- **Approve:** commit the spec files on the feature branch (`git add openspec docs` then commit `<change-id>: add specs and plan`), set the roadmap status to `In Progress`, and continue to Phase 4.
- **Change:** apply the requested edits to the specs/plan, then present the updated plan and ask again.
- **Stop:** leave everything uncommitted on the branch and end. Report what exists.

Approving the plan authorises **local commits on the feature branch only**. It never authorises pushing, merging, or touching `main`.

## Phase 4 — Implement

1. Work through `tasks.md` **in order**. After each task is done **and verified**, tick it (`- [x]`).
2. Follow `openspec/project.md` conventions and the stack it names. Use `.venv/bin/python` / `.venv/bin/pip` for local Python commands.
3. Every spec scenario gets at least one automated test, named after the scenario.
4. Verify against the local stack: `make up` (or the profile the roadmap row names), then the API tests. Fix failures before ticking the related tasks.
5. Commit at the end of each task group: `<change-id>: <imperative summary>`.
6. If the implementation must diverge from the specs or the HLD/LLD, **stop and ask** before diverging. After approval, update the spec and the docs in the same commit as the code.
7. If something cannot be made to work, stop and report it honestly with the failing output. Do not tick the task.

## Phase 5 — Wrap up

1. Run the full test suite one last time and check the Compose stack comes up healthy from scratch (`make nuke && make up`).
2. Report:
   - Branch name and commits made.
   - Tasks done, and any not done with the reason.
   - Test results (counts, any failures).
   - How the user can try the feature locally (commands and example `curl`s).
3. Ask whether to **archive** the change (move it to `openspec/changes/archive/<YYYY-MM-DD>-<change-id>/`, merge its deltas into `openspec/specs/`, set the roadmap status to `Done`, commit). Archive only on a yes.
4. Merging into `main` and pushing are left to the user unless they explicitly ask.
