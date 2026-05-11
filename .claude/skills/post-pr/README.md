# Post-PR Skill

Consolidates post-PR-creation bookkeeping into a single efficient operation, reducing 5-6 sequential tool calls into one script execution.

## Quick Start

```bash
# Install dependencies with uv
uv sync

# Run tests
uv run pytest -v

# Run with coverage
uv run pytest --cov=scripts --cov-report=html -v

# Execute workflow
uv run python scripts/post_pr_operations.py \
  https://github.com/RedHatInsights/hcc-ai-assistant/pull/123 \
  123 \
  RHCLOUD-456 \
  "Add vector search caching"
```

## What It Does

After creating a PR, this skill executes 6 operations in sequence:

1. **task_update** - Update GitHub PR (add labels, JIRA link, request reviewers)
2. **jira_transition_issue** - Move JIRA ticket to "Code Review" (JIRA Cloud API v3)
3. **jira_add_comment** - Post PR link and summary to JIRA (ADF format)
4. **slack_notify** - Send notification to Slack webhook
5. **memory_store** - Save implementation learnings to JSON file
6. **bot_status_update** - Update bot status to `idle`

All operations use **fail-fast error handling**: if any operation fails, execution stops immediately.

## Usage

### From Claude Code

```bash
/post-pr https://github.com/RedHatInsights/hcc-ai-assistant/pull/123 123 RHCLOUD-456 "Add caching"
```

### From Command Line

```bash
# Basic usage
python scripts/post_pr_operations.py PR_URL PR_NUMBER TICKET_ID SUMMARY

# With options
uv run python scripts/post_pr_operations.py \
  https://github.com/RedHatInsights/hcc-ai-assistant/pull/124 \
  124 \
  RHCLOUD-457 \
  "Fix timeout" \
  --reviewers=user1,user2 \
  --slack-channel=#hcc-alerts \
  --skip=slack,memory \
  --dry-run

# JSON output
uv run python scripts/post_pr_operations.py \
  https://github.com/RedHatInsights/hcc-ai-assistant/pull/125 \
  125 \
  RHCLOUD-458 \
  "Update deps" \
  --json
```

### From Python

```python
from scripts.post_pr_operations import execute_post_pr_workflow

result = execute_post_pr_workflow(
    pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/123",
    pr_number=123,
    ticket_id="RHCLOUD-456",
    summary="Add vector search caching",
    github_token=None,  # Falls back to GITHUB_TOKEN env var
    jira_token=None,    # Falls back to POST_PR_JIRA_TOKEN env var
    slack_channel="#hcc-ai-assistant",
    reviewers=["user1", "user2"],
    skip_operations=[],
    dry_run=False,
)

if result.success:
    print("✓ All operations completed successfully")
    for op in result.operations:
        print(f"  {op.operation}: {op.message}")
else:
    print("✗ Workflow failed")
    for op in result.operations:
        if op.status.value == "failed":
            print(f"  {op.operation}: {op.message}")
```

## Configuration

Set these environment variables for API integrations:

```bash
# GitHub (required)
export GITHUB_TOKEN=ghp_your_token_here  # Or GH_TOKEN

# JIRA Cloud (required for JIRA operations)
export POST_PR_JIRA_TOKEN=your_api_token_here
export POST_PR_JIRA_EMAIL=your.email@redhat.com
export POST_PR_JIRA_URL=https://redhat.atlassian.net  # Optional, this is the default

# Slack (required for Slack notifications)
export POST_PR_SLACK_WEBHOOK=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# Storage (optional)
export POST_PR_MEMORY_STORE=/path/to/memory.json  # Default: /tmp/memory.json
```

**Note:** Uses JIRA Cloud API v3 with Basic authentication (email + API token).

## Testing

```bash
# Run all tests (35 total)
uv run pytest -v

# Run specific test file
uv run pytest tests/test_operations.py -v  # 23 unit tests
uv run pytest tests/test_integration.py -v  # 12 integration tests

# Run with coverage
uv run pytest --cov=scripts --cov-report=html -v

# View coverage report
open htmlcov/index.html
```

### Test Coverage

- **Unit tests** (`test_operations.py`): 23 tests for individual operations
  - Verifies exact API URLs, headers, JSON payloads
  - Tests error handling and edge cases
  - Validates GitHub, JIRA, and Slack integrations

- **Integration tests** (`test_integration.py`): 12 tests for full workflow
  - End-to-end scenarios with mocked APIs
  - Tests fail-fast behavior
  - Validates skip operations and dry-run mode

## Architecture

### Design Principles

1. **Fail fast**: Stop on first error to maintain consistency
2. **No LLM reasoning**: All inputs known at PR creation time
3. **Sequential execution**: Operations have dependencies (e.g., JIRA transition before comment)
4. **Idempotent**: Safe to retry on failure
5. **Observable**: Logs all actions to stdout
6. **Testable**: Comprehensive unit and integration tests

### File Structure

```
.claude/skills/post-pr/
├── SKILL.md                     # Skill documentation (Claude Code entrypoint)
├── README.md                    # This file
├── pyproject.toml               # Dependencies and tool config
├── uv.lock                      # Locked dependencies
├── scripts/
│   ├── __init__.py
│   └── post_pr_operations.py   # Main implementation (~700 lines)
└── tests/
    ├── __init__.py
    ├── test_operations.py       # Unit tests (23 tests)
    └── test_integration.py      # Integration tests (12 tests)
```

### Dependencies

- **Python 3.12+**: Modern type hints and language features
- **httpx >= 0.27.0**: HTTP client for GitHub, JIRA, and Slack APIs
- **pytest >= 8.0.0**: Testing framework (dev dependency)
- **pytest-cov >= 4.1.0**: Coverage reporting (dev dependency)

Managed with **uv** for fast, reliable dependency resolution.

## API Integrations

### GitHub REST API
- **Authentication**: Bearer token (GITHUB_TOKEN or GH_TOKEN)
- **Operations**: Add labels, update PR description, request reviewers
- **Endpoint**: `https://api.github.com`

### JIRA Cloud API v3
- **Authentication**: Basic (email:token)
- **Transitions**: `GET/POST /rest/api/3/issue/{key}/transitions`
- **Comments**: Atlassian Document Format (ADF)
- **Endpoint**: `https://redhat.atlassian.net` (default)
- **Note**: Requires API v3 (v2 returns empty transitions array)

### Slack Webhooks
- **Format**: Incoming webhook with attachment format
- **Fields**: PR number, link, summary
- **Color coding**: "good" (green) for PR created events

## Troubleshooting

### Common Issues

**Error: "GitHub token not configured"**
- Set `GITHUB_TOKEN` or `GH_TOKEN` environment variable
- Or pass `--github-token` parameter

**Error: "JIRA token not configured"**
- Set `POST_PR_JIRA_TOKEN` and `POST_PR_JIRA_EMAIL` environment variables
- Or run with `--skip=jira` to skip JIRA operations
- **Note**: Requires JIRA Cloud API v3 (redhat.atlassian.net)

**Error: "Slack webhook not configured"**
- Set `POST_PR_SLACK_WEBHOOK` environment variable
- Or run with `--skip=slack` to skip Slack notifications

**JIRA transitions return empty array**
- Ensure using JIRA Cloud API v3 (not v2)
- Check `POST_PR_JIRA_URL` is set to `https://redhat.atlassian.net`
- Verify email and token are correct for Basic auth

**Workflow stops partway through**
- This is expected behavior (fail-fast)
- Check error message to identify which operation failed
- Fix the issue and re-run the workflow

### Dry Run Mode

Use `--dry-run` to preview what would happen without executing:

```bash
uv run python scripts/post_pr_operations.py PR_URL PR_NUMBER TICKET_ID SUMMARY --dry-run
```

This logs all actions but doesn't make API calls or write files.

## Contributing

### Code Style

- **Line length**: 120 characters (black + ruff)
- **Type hints**: Required for all functions
- **Docstrings**: Google style for all public functions
- **Tests**: Required for all new operations

### Adding New Operations

1. Add method to `PostPROperations` class
2. Update `execute_post_pr_workflow` to call the new operation
3. Add unit tests in `tests/test_operations.py`
4. Add integration tests in `tests/test_integration.py`
5. Update SKILL.md documentation

### Running Tests Before Commit

```bash
# Lint and auto-fix
uv run ruff check --fix scripts/

# Run tests
uv run pytest -v

# Check coverage
uv run pytest --cov=scripts --cov-report=term-missing -v
```

**Note**: Line length is configured to 120 characters in pyproject.toml.

## License

Same as parent project (hcc-ai-assistant).
