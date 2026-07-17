# devin-action

Custom GitHub Action that starts a [Devin](https://devin.ai/) session from
`issue_comment`, `pull_request`, `pull_request_review_comment`, `push`, and
`check_run` events using the Devin API.

Give your team the same behavior as Devin's built-in GitHub Automation, but with
triggers and conditions you control from your own workflow file.

## Authentication

Devin authenticates against your repository via its own official GitHub App installation, so make
sure the Devin GitHub App is installed and granted access to the target
repositories in your organization. The action itself only talks to the Devin
API — no GitHub token, PAT, or App credentials need to be supplied.

## Features

- Recognizes `/devin <prompt>` from comments (prefix configurable).
- Extracts a normalized context from each supported event and hands Devin a
  well-scoped prompt.
- Reuses the same Devin session per PR/Issue thread on follow-up comments,
  falling back to a fresh one when the previous session is gone. See
  [Session reuse](#session-reuse).
- Optionally asks Devin to post progress reports to the originating
  PR/Issue at task checkpoints. See [Progress reports](#progress-reports).
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
| `report` | no | `false` | Ask Devin to post progress-report comments to the originating PR/Issue at natural task checkpoints. |

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

Session lookup uses Devin's list-sessions endpoint filtered by the thread
tag, so **no GitHub token is required**; the `reused` output tells the
caller which path was taken. `push` and `check_run` triggers always create
a new session — they are not tied to a thread.

- **Force a new session**: send `/devin new <prompt>`. The `new` keyword is
  stripped from the prompt before it reaches Devin.
- **Disable reuse globally**: set `session-reuse: false`.

## Progress reports

When `report: true`, the action instructs Devin to post progress-report
comments to the originating PR/Issue via its GitHub App at natural
checkpoints — after finishing a sub-task, after pushing a commit, when
handing control back, or when all requested work is complete. Devin
decides when to post; the action itself does no runtime polling and never
posts comments directly.

Each report follows a fixed template with a requirements checklist, a
"what just changed" summary, next steps, and session info (elapsed time,
ACU used, session URL). Each comment also carries a hidden marker
`<!-- devin-action:progress-report:pr={owner}/{repo}#{n}:session={id} -->`
so future sessions can locate their predecessors.

**Multi-session PRs**: a single PR can span multiple Devin sessions
(previous session completed or errored, `session-reuse: false`, or
`/devin new`). Each session posts its own reports. Session-scoped metrics
(elapsed time, ACU) reset per session — cumulative values across sessions
are not tracked. The requirements checklist is carried forward: the new
session scans the thread for prior reports and continues from them
rather than restarting.

Requires the Devin GitHub App to have write access to issues/PRs in the
repository (it already does if Devin can open PRs there).

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

Combined with `allowed-associations`, this mitigates the common
"external contributor injects adversarial `/devin` instructions" attack.
Tune `allowed-associations` to your project's threat model.

## Development

Requires Python 3.12.

```
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

The `Dockerfile` remains the source for local development and the Docker build
in CI. Published versions of the action use the pre-built
`ghcr.io/knanao/devin-action:v1` image, so consumers do not rebuild it on every
run.

## Releasing

Releases are deliberate versioned events rather than a side effect of every
merge to `main`. Publish a semantic version tag to build and push the image and
create the matching GitHub Release with generated release notes:

```sh
git tag v1.2.3
git push origin v1.2.3
```

The release workflow publishes the immutable version tag (`v1.2.3`), moving
major tag (`v1`), and `latest` to
`ghcr.io/knanao/devin-action`. The action descriptor follows the moving major
tag so `knanao/devin-action@v1` and its container image advance together.

The workflow can also be started manually with **Actions → Release → Run
workflow** and an explicit `vMAJOR.MINOR.PATCH` version. The manual path creates
the version tag at the selected branch or commit as part of creating the
release.

After the package is first published, set its visibility to **Public** in the
package settings so GitHub Actions consumers can pull the image without
authentication.
