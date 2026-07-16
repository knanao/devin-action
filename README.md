# devin-action

Custom GitHub Action that starts a [Devin](https://devin.ai/) session from
`issue_comment`, `pull_request`, `pull_request_review_comment`, `push`, and
`check_run` events using the Devin API.

Give your team the same behavior as Devin's built-in GitHub Automation, but with
triggers and conditions you control from your own workflow file.

## Authentication

This action does **not** pass any GitHub token to Devin. Devin authenticates
against your repository via its own official GitHub App installation, so make
sure the Devin GitHub App is installed and granted access to the target
repositories in your organization. The action itself only talks to the Devin
API — no GitHub token, PAT, or App credentials need to be supplied.

## Features

- Recognizes `/devin <prompt>` from comments (prefix configurable).
- Extracts a normalized context from each supported event and hands Devin a
  well-scoped prompt.
- **Reuses the same Devin session per PR/Issue thread** so follow-up
  `/devin` comments continue the ongoing conversation instead of restarting
  from scratch. Falls back to a new session automatically if the previous
  one has finished or errored. Force a fresh session with `/devin new
  <prompt>`, or disable reuse entirely via `session-reuse: false`.
- Sanitizes untrusted user content and wraps it in a `<user_input>` block so
  comment bodies cannot override operator instructions.
- Filters comment triggers by `author_association` (default:
  `OWNER,MEMBER,COLLABORATOR`).
- Maps Devin API errors (`401`/`403`/`404`/`422`/`429`/5xx) to actionable
  messages surfaced through the GitHub Actions log.

## Usage

```yaml
name: devin
on:
  issue_comment:
    types: [created]

jobs:
  devin:
    if: github.event.issue.pull_request == null && startsWith(github.event.comment.body, '/devin')
    runs-on: ubuntu-latest
    steps:
      - uses: knanao/devin-action@v1
        with:
          devin-api-key: ${{ secrets.DEVIN_API_KEY }}
          devin-org-id:  ${{ secrets.DEVIN_ORG_ID }}
```

A more complete example covering PRs, review comments, and failing checks lives
in [`examples/devin.yml`](examples/devin.yml).

## Inputs

| name | required | default | description |
|---|---|---|---|
| `devin-api-key` | yes | — | Devin service-user API key (`cog_*`). |
| `devin-org-id` | yes | — | Devin organization id (`org_*`). |
| `prompt-prefix` | no | `/devin` | Comment trigger prefix. |
| `additional-instructions` | no | `""` | Always-appended operator instructions. |
| `devin-mode` | no | `normal` | One of `normal` / `fast` / `lite` / `ultra` / `fusion`. |
| `max-acu-limit` | no | — | Integer ACU limit for the session. |
| `tags` | no | `github-action` | Comma-separated tags. |
| `playbook-id` | no | — | Optional Devin playbook id. |
| `allowed-associations` | no | `OWNER,MEMBER,COLLABORATOR` | Comma-separated `author_association` filter for issue/PR comment triggers. |
| `api-version` | no | `v3` | Devin API version to call. Only `v1` or `v3` are accepted. |
| `session-reuse` | no | `true` | Reuse an existing Devin session for the same PR/Issue thread when one is live. |

## Outputs

| name | description |
|---|---|
| `session-id` | Devin session id (empty when skipped). |
| `session-url` | Devin session URL (empty when skipped). |
| `skipped` | `true` when the action did not send a request to Devin. |
| `reused` | `true` when the prompt was delivered to an existing session instead of creating a new one. |

## Session reuse

For `issue_comment`, `pull_request_review_comment`, and `pull_request`
triggers, the action tags each session it creates with
`devin-action:thread:{owner}/{repo}#{number}`. On the next trigger for the
same thread it queries the Devin API for a still-live session carrying that
tag and, if found, sends the new prompt as a follow-up message via
`POST .../sessions/{id}/message[s]`. Devin retains its previous plan,
repository index, and conversation history — cheaper and more coherent than
starting over.

- **Force a new session**: send `/devin new <prompt>`. The `new`
  keyword is stripped from the prompt before it reaches Devin.
- **Disable reuse globally**: set `session-reuse: false` in the workflow.
- **`push` and `check_run` triggers always create a new session** — they
  are not tied to a thread.
- Session lookup is done via Devin's list-sessions endpoint filtered by
  the thread tag; **no GitHub token is required**. The `reused` output
  tells the caller which path was taken.

## Prompt safety

User-authored content (issue body, PR title/body, comment body, commit message)
is treated as **data**, not instructions:

- Unicode is NFKC-normalized; zero-width, bidi, and control characters are
  stripped.
- Any `</user_input>` closing tags in user content are escaped so untrusted
  text cannot break out of its delimiter.
- Content is truncated to 16 KiB with a `[truncated]` marker.
- The prompt template explicitly tells Devin that content inside
  `<user_input>` is untrusted data.

Combined with the `allowed-associations` filter, this blocks the common
"external contributor commented `/devin` with adversarial instructions" class of
attack. Configure this input to your project's threat model.

## Development

Requires Python 3.12.

```
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

The Docker image is built directly from `Dockerfile` by GitHub Actions when the
action is consumed (`runs.image: Dockerfile`), so no image publishing is
required.
