"""Integration tests for post-PR workflow."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scripts.post_pr_operations import OperationStatus, execute_post_pr_workflow


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def env_vars(temp_dir):
    """Set up environment variables for testing."""
    original_env = os.environ.copy()

    os.environ["GITHUB_TOKEN"] = "test-github-token"
    os.environ["POST_PR_JIRA_URL"] = "https://test-jira.example.com"
    os.environ["POST_PR_JIRA_TOKEN"] = "test-jira-token"
    os.environ["POST_PR_SLACK_WEBHOOK"] = "https://hooks.slack.com/test"
    os.environ["POST_PR_MEMORY_STORE"] = str(temp_dir / "memory.json")

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_github_api():
    """Mock GitHub and JIRA API responses."""
    with patch("scripts.post_pr_operations.httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock GitHub responses
        mock_post_response = Mock()
        mock_post_response.raise_for_status = Mock()

        mock_get_response = Mock()
        mock_get_response.raise_for_status = Mock()

        # Will be called for both GitHub PR GET and JIRA transitions GET
        def get_side_effect(url, **kwargs):
            response = Mock()
            response.raise_for_status = Mock()
            if "github.com" in url:
                response.json.return_value = {"body": "Existing PR description"}
            elif "transitions" in url:
                # JIRA transitions response
                response.json.return_value = {
                    "transitions": [
                        {"id": "21", "to": {"name": "Code Review"}},
                    ]
                }
            return response

        mock_patch_response = Mock()
        mock_patch_response.raise_for_status = Mock()

        mock_client.post.return_value = mock_post_response
        mock_client.get.side_effect = get_side_effect
        mock_client.patch.return_value = mock_patch_response

        yield mock_client


class TestFullWorkflow:
    """Test complete post-PR workflow."""

    def test_successful_workflow(self, env_vars, temp_dir, mock_github_api):
        """Test successful execution of all operations."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/123",
            pr_number=123,
            ticket_id="TICKET-456",
            summary="Add vector search caching",
            slack_channel="#hcc-ai-assistant",
            reviewers=["user1", "user2"],
        )

        assert result.success is True
        assert result.pr_url == "https://github.com/RedHatInsights/hcc-ai-assistant/pull/123"
        assert result.pr_number == 123
        assert result.ticket_id == "TICKET-456"
        assert len(result.operations) == 6

        # Verify all operations succeeded
        for op in result.operations:
            assert op.status == OperationStatus.SUCCESS

        # Verify operation order
        expected_operations = [
            "task_update",
            "jira_transition_issue",
            "jira_add_comment",
            "slack_notify",
            "memory_store",
            "bot_status_update",
        ]
        actual_operations = [op.operation for op in result.operations]
        assert actual_operations == expected_operations

    def test_workflow_with_skip_operations(self, env_vars, mock_github_api):
        """Test workflow with some operations skipped."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/124",
            pr_number=124,
            ticket_id="TICKET-457",
            summary="Fix timeout",
            skip_operations=["slack", "memory"],
        )

        assert result.success is True

        # Verify skipped operations
        slack_op = next(op for op in result.operations if op.operation == "slack_notify")
        assert slack_op.status == OperationStatus.SKIPPED

        memory_op = next(op for op in result.operations if op.operation == "memory_store")
        assert memory_op.status == OperationStatus.SKIPPED

        # Verify non-skipped operations succeeded
        for op in result.operations:
            if op.operation not in ["slack_notify", "memory_store"]:
                assert op.status == OperationStatus.SUCCESS

    def test_workflow_dry_run(self, env_vars, temp_dir, mock_github_api):
        """Test workflow in dry-run mode."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/125",
            pr_number=125,
            ticket_id="TICKET-458",
            summary="Update dependencies",
            dry_run=True,
        )

        assert result.success is True

        # Verify all operations succeeded (dry-run should not fail)
        for op in result.operations:
            assert op.status == OperationStatus.SUCCESS

        # Verify no files were created (memory store shouldn't exist in dry-run)
        memory_store = Path(os.environ["POST_PR_MEMORY_STORE"])
        assert not memory_store.exists()

    def test_workflow_fails_fast_on_error(self, temp_dir, monkeypatch):
        """Test that workflow stops on first error (fail-fast)."""
        # Clear all token environment variables
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("POST_PR_JIRA_TOKEN", raising=False)
        monkeypatch.delenv("POST_PR_SLACK_WEBHOOK", raising=False)

        # Set up minimal environment
        monkeypatch.setenv("POST_PR_MEMORY_STORE", str(temp_dir / "memory.json"))

        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/126",
            pr_number=126,
            ticket_id="TICKET-459",
            summary="Test failure",
        )

        assert result.success is False

        # Find the failed operation (should be first one - task_update)
        failed_ops = [op for op in result.operations if op.status == OperationStatus.FAILED]
        assert len(failed_ops) > 0
        assert failed_ops[0].operation == "task_update"

        # Verify workflow stopped after failure (only 1 operation ran)
        assert len(result.operations) == 1

    def test_workflow_result_serialization(self, env_vars, mock_github_api):
        """Test that workflow result can be serialized to JSON."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/127",
            pr_number=127,
            ticket_id="TICKET-460",
            summary="Test serialization",
        )

        # Convert to dict and serialize
        result_dict = result.to_dict()
        json_str = json.dumps(result_dict, indent=2)

        # Verify JSON is valid and can be parsed
        parsed = json.loads(json_str)
        assert parsed["success"] is True
        assert parsed["pr_number"] == 127
        assert parsed["ticket_id"] == "TICKET-460"
        assert len(parsed["operations"]) == 6


class TestWorkflowEdgeCases:
    """Test edge cases and error scenarios."""

    def test_workflow_with_minimal_inputs(self, env_vars, mock_github_api):
        """Test workflow with only required inputs."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/128",
            pr_number=128,
            ticket_id="TICKET-461",
            summary="Minimal test",
        )

        assert result.success is True
        # Should use default Slack channel
        slack_op = next(op for op in result.operations if op.operation == "slack_notify")
        assert slack_op.details["channel"] == "#hcc-ai-assistant"

    def test_workflow_with_long_summary(self, env_vars, mock_github_api):
        """Test workflow with very long PR summary."""
        long_summary = "A" * 1000  # 1000 character summary

        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/129",
            pr_number=129,
            ticket_id="TICKET-462",
            summary=long_summary,
        )

        assert result.success is True

        # Verify summary is preserved in operations
        jira_comment_op = next(op for op in result.operations if op.operation == "jira_add_comment")
        assert long_summary in jira_comment_op.details["comment"]

    def test_workflow_with_special_characters(self, env_vars, mock_github_api):
        """Test workflow with special characters in summary."""
        special_summary = 'Test "quotes" & <tags> and \\backslashes\\'

        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/130",
            pr_number=130,
            ticket_id="TICKET-463",
            summary=special_summary,
        )

        assert result.success is True

        # Verify PR was updated successfully
        task_op = next(op for op in result.operations if op.operation == "task_update")
        assert task_op.status == OperationStatus.SUCCESS
        assert task_op.details["jira_ticket"] == "TICKET-463"

    def test_workflow_with_reviewers(self, env_vars, mock_github_api):
        """Test workflow with reviewers specified."""
        result = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/131",
            pr_number=131,
            ticket_id="TICKET-464",
            summary="Reviewer test",
            reviewers=["reviewer1", "reviewer2", "reviewer3"],
        )

        assert result.success is True

        task_op = next(op for op in result.operations if op.operation == "task_update")
        assert "reviewer1" in str(task_op.details.get("reviewers_requested", []))


class TestWorkflowPersistence:
    """Test that workflow operations persist data correctly."""

    def test_github_pr_updates(self, env_vars, mock_github_api):
        """Test that GitHub PR updates include correct details."""
        result1 = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/132",
            pr_number=132,
            ticket_id="TICKET-465",
            summary="First PR",
            reviewers=["user1"],
        )

        result2 = execute_post_pr_workflow(
            pr_url="https://github.com/test/other-repo/pull/133",
            pr_number=133,
            ticket_id="TICKET-466",
            summary="Second PR",
            reviewers=["user2", "user3"],
        )

        assert result1.success is True
        assert result2.success is True

        # Verify first PR update
        task_op1 = next(op for op in result1.operations if op.operation == "task_update")
        assert task_op1.details["owner"] == "RedHatInsights"
        assert task_op1.details["repo"] == "hcc-ai-assistant"
        assert task_op1.details["pr_number"] == 132
        assert task_op1.details["reviewers_requested"] == ["user1"]

        # Verify second PR update
        task_op2 = next(op for op in result2.operations if op.operation == "task_update")
        assert task_op2.details["owner"] == "test"
        assert task_op2.details["repo"] == "other-repo"
        assert task_op2.details["pr_number"] == 133
        assert task_op2.details["reviewers_requested"] == ["user2", "user3"]

    def test_memory_accumulation(self, env_vars, temp_dir, mock_github_api):
        """Test that memories accumulate over multiple executions."""
        # Execute workflow 3 times
        for i in range(3):
            result = execute_post_pr_workflow(
                pr_url=f"https://github.com/RedHatInsights/hcc-ai-assistant/pull/{134 + i}",
                pr_number=134 + i,
                ticket_id=f"TICKET-{467 + i}",
                summary=f"PR {i + 1}",
            )
            assert result.success is True

        # Verify all memories are stored
        memory_store = Path(os.environ["POST_PR_MEMORY_STORE"])
        assert memory_store.exists()

        with open(memory_store, "r") as f:
            memories = json.load(f)
            assert len(memories) == 3

            for i, memory in enumerate(memories):
                assert memory["ticket_id"] == f"TICKET-{467 + i}"
                assert memory["pr_url"] == f"https://github.com/RedHatInsights/hcc-ai-assistant/pull/{134 + i}"

    def test_bot_status_overwrite(self, env_vars, mock_github_api):
        """Test that bot status is overwritten on each execution."""
        result1 = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/137",
            pr_number=137,
            ticket_id="TICKET-470",
            summary="First",
        )

        result2 = execute_post_pr_workflow(
            pr_url="https://github.com/RedHatInsights/hcc-ai-assistant/pull/138",
            pr_number=138,
            ticket_id="TICKET-471",
            summary="Second",
        )

        assert result1.success is True
        assert result2.success is True

        # Verify status file contains latest status
        status_file = Path("/tmp/bot_status.json")
        assert status_file.exists()

        with open(status_file, "r") as f:
            status = json.load(f)
            assert status["status"] == "idle"
            # Should be recent timestamp from second execution
            assert status["timestamp"]
