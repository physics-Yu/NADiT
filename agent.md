# Engineering Standards (AI Collaboration Rules)

## 0. Communication
- Respond to me in Simplified Chinese.
- Write code, comments, commit messages, and docs in English.

## 1. Core Principles
- **Small steps**: each change solves one problem and can be tested and reverted independently.
- **Verify before claiming done**: never say "done" or "fixed" without running the tests.
- **Ask when uncertain**: stop and confirm on ambiguous requirements, architectural changes, or code deletion.
- **Report honestly**: state failed tests, skipped steps, and unverified parts explicitly, with actual output.

## 2. Workflow (mandatory for every task)
1. **Understand**: restate the requirement; list assumptions and affected files.
2. **Plan**: for changes touching >3 files or >50 lines, present a plan and wait for my approval.
3. **Baseline**: run the existing test suite before changing anything; record pass/fail counts.
4. **Test first**: write tests for new features; for bugs, first write a failing test that reproduces it.
5. **Implement**: make the minimal necessary change. Do not refactor unrelated code.
6. **Verify**: run the full suite and compare with the baseline. No new failures allowed.
7. **Commit**: only after tests pass, following Section 5.
8. **Report**: use the format in Section 9.

## 3. Testing
- Framework: pytest (replace per project).
- Every new or modified public function must have tests. Every bug fix must include a regression test.
- Cover the happy path, edge cases, and invalid input.
- Tests must be deterministic: fix random seeds; no dependence on network, wall-clock time, or execution order.
- Never change a test's assertions just to make it pass, unless you explain why and I approve.
- Never use skip/xfail to hide failures without my approval.
- Commands:
  - Full suite: `pytest -q`
  - Quick check: `pytest -q -x --lf`

## 4. Rollback & Safety
- Before starting, confirm the working tree is clean (`git status`). If not, ask me.
- Work on a dedicated branch per task: `feat/xxx`, `fix/xxx`, `refactor/xxx`.
- Commit after each verifiable step so there is always a known-good state to return to.
- If tests go from passing to failing and two fix attempts don't resolve it:
  stop, propose reverting to the last passing commit, and explain the cause. Ask before running `git reset --hard`.
- Always ask before:
  - `git push`, `git push --force`, `git reset --hard`, `git rebase`
  - Deleting files or directories
  - Changing dependencies (requirements / pyproject / package.json)
  - Changing CI config, database schemas, or public interfaces
- Never commit secrets, tokens, large data files, or build artifacts.

## 5. Commit Messages (Conventional Commits)
Format: `<type>(<scope>): <short summary>`
- `feat`: new feature
- `fix`: bug fix
- `test`: tests only
- `refactor`: restructuring, no behavior change
- `docs`: documentation
- `chore`: build, dependencies, misc
- `perf`: performance

One logical change per commit. The body explains *why*, not just *what*.

## 6. Code Quality
- Formatter: `ruff format`; linter: `ruff check`; type checker: `mypy` (replace per project).
- Must pass before commit: `ruff format --check . && ruff check . && mypy . && pytest -q`
- Public functions and classes require type hints and docstrings (args, returns, raises).
- Use clear names without cryptic abbreviations. Functions do one thing; consider splitting beyond ~50 lines.
- Never swallow exceptions: no bare `except:`. Error messages must include context.
- No dead code, debug prints, or commented-out code blocks.
- Extract magic numbers into named constants or config.
- Match the style of surrounding code; reuse existing utilities before writing new ones.

## 7. Documentation
- Update README and docstrings when public interfaces change.
- Record significant design decisions in `docs/decisions/NNNN-title.md`
  (ADR: context, decision, alternatives, consequences).
- Add user-visible changes to the `Unreleased` section of `CHANGELOG.md`.

## 8. Never
- Claim "it should work now" without running tests.
- Hardcode expected values or special-case test inputs to pass tests.
- Rewrite large amounts of code that weren't part of the request.
- Add a dependency without justifying it.
- Guess at uncertain APIs or parameters. Check the docs or ask.

## 9. Report Format (end of every task)
- **Changes**: files touched and what changed in each
- **Verification**: commands run and results (pass/fail counts vs. baseline)
- **Commits**: hash and message
- **Open items / Risks**: what's not done, not verified, or needs my decision
