# devin-action

Custom GitHub Action that starts a [Devin](https://devin.ai/) session from
`issue_comment`, `pull_request`, `pull_request_review_comment`, `push`, and
`check_run` events using the Devin API.

Give your team the same behavior as Devin's built-in GitHub Automation, but with
triggers and conditions you control from your own workflow file.

## Features

- Recognizes `/devin <prompt>` from comments (prefix configurable).
- Extracts a normalized context from each supported event and hands Devin a
  well-scoped prompt.
- Sanitizes untrusted user content and wraps it in a `<user_input>` block so
  comment bodies cannot override operator instructions.
- Filters comment triggers by `author_association` (default:
  `OWNER,MEMBER,COLLABORATOR`).
- Optionally posts a tracking comment linking to the session, and instructs
  Devin to hide it via `minimizeComment` when the session completes.
- Maps Devin API errors (`401`/`403`/`404`/`422`/`429`/5xx) to actionable
  messages and posts a failure comment when possible.

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
    permissions:
      issues: write
      pull-requests: write
      contents: read
    steps:
      - uses: knanao/devin-action@v1
        with:
          devin-api-key: ${{ secrets.DEVIN_API_KEY }}
          devin-org-id:  ${{ secrets.DEVIN_ORG_ID }}
          github-token:  ${{ secrets.GITHUB_TOKEN }}
```

A more complete example covering PRs, review comments, and failing checks lives
in [`examples/devin.yml`](examples/devin.yml).

## Inputs

| name | required | default | description |
|---|---|---|---|
| `devin-api-key` | yes | — | Devin service-user API key (`cog_*`). |
| `devin-org-id` | yes | — | Devin organization id (`org_*`). |
| `github-token` | yes | — | Token passed to Devin as `$GITHUB_TOKEN` session secret. Usually `${{ secrets.GITHUB_TOKEN }}` or a PAT. |
| `prompt-prefix` | no | `/devin` | Comment trigger prefix. |
| `additional-instructions` | no | `""` | Always-appended operator instructions. |
| `devin-mode` | no | `normal` | One of `normal` / `fast` / `lite` / `ultra` / `fusion`. |
| `max-acu-limit` | no | — | Integer ACU limit for the session. |
| `tags` | no | `github-action` | Comma-separated tags. |
| `playbook-id` | no | — | Optional Devin playbook id. |
| `allowed-associations` | no | `OWNER,MEMBER,COLLABORATOR` | Comma-separated `author_association` filter for issue/PR comment triggers. |
| `post-comment` | no | `true` | Post a tracking comment on the Issue/PR and ask Devin to hide it on success. |
| `api-version` | no | `v3` | Devin API version to call. Only `v1` or `v3` are accepted. |

## Outputs

| name | description |
|---|---|
| `session-id` | Devin session id (empty when skipped). |
| `session-url` | Devin session URL (empty when skipped). |
| `skipped` | `true` when the action did not send a request to Devin. |

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

## Session cleanup

When `post-comment=true` and the event has an Issue/PR number:

1. The action generates a `tracker_id` (UUID4) per invocation.
2. It posts a tracking comment with the session URL and an HTML-comment marker
   `<!-- devin-action:tracker=<id> -->`.
3. It appends a `[Cleanup]` block to the prompt instructing Devin — as its
   final step, only on success — to locate the comment via the marker and hide
   it with `minimizeComment(subjectId, RESOLVED)` using `$GITHUB_TOKEN`.

If the session errors or Devin fails to run the cleanup, the tracking comment
stays visible — useful for debugging.

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
