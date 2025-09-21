"""Performance tests for gh CLI execution."""
import pytest
import time
from unittest.mock import Mock, patch

from untaped_github.gh_cli_wrapper import GitHubCliWrapper


@pytest.mark.performance
class TestGhCliPerformance:
    """Performance tests for GitHub CLI operations."""

    @patch('subprocess.run')
    def test_gh_command_execution_performance(self, mock_run):
        """Test that gh command execution completes in under 5 seconds."""
        # Mock successful command execution
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"result": "success"}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        start_time = time.time()
        result = wrapper._run_gh_command(['api', 'user'])
        end_time = time.time()

        execution_time = (end_time - start_time) * 1000  # Convert to milliseconds
        assert execution_time < 5000, f"gh command execution took {execution_time".2f"}ms, expected < 5000ms"

    @patch('subprocess.run')
    def test_api_get_performance(self, mock_run):
        """Test that API GET requests complete in under 5 seconds."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"login": "testuser", "id": 123}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        start_time = time.time()
        result = wrapper.api_get('user')
        end_time = time.time()

        execution_time = (end_time - start_time) * 1000
        assert execution_time < 5000, f"API GET took {execution_time".2f"}ms, expected < 5000ms"

    @patch('subprocess.run')
    def test_api_get_raw_performance(self, mock_run):
        """Test that API GET raw requests complete in under 5 seconds."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = 'File content here'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        start_time = time.time()
        result = wrapper.api_get_raw('repos/owner/repo/contents/file.txt')
        end_time = time.time()

        execution_time = (end_time - start_time) * 1000
        assert execution_time < 5000, f"API GET raw took {execution_time".2f"}ms, expected < 5000ms"

    @patch('subprocess.run')
    def test_multiple_gh_commands_performance(self, mock_run):
        """Test performance of multiple sequential gh commands."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"result": "success"}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        # Execute multiple commands
        commands = [
            ['api', 'user'],
            ['api', 'repos/owner/repo'],
            ['api', 'repos/owner/repo/contents/README.md'],
            ['auth', 'status']
        ]

        start_time = time.time()

        for cmd in commands:
            wrapper._run_gh_command(cmd)

        end_time = time.time()

        total_time = (end_time - start_time) * 1000
        avg_time_per_command = total_time / len(commands)

        assert total_time < 10000, f"Total time for {len(commands)} commands: {total_time".2f"}ms, expected < 10000ms"
        assert avg_time_per_command < 2500, f"Average time per command: {avg_time_per_command".2f"}ms, expected < 2500ms"

    @patch('subprocess.run')
    def test_gh_command_timeout_handling(self, mock_run):
        """Test that gh commands handle timeouts appropriately."""
        def slow_command(*args, **kwargs):
            time.sleep(6)  # Simulate slow command
            mock_process = Mock()
            mock_process.returncode = 0
            mock_process.stdout = '{"result": "success"}'
            mock_process.stderr = ''
            return mock_process

        mock_run.side_effect = slow_command

        wrapper = GitHubCliWrapper()

        start_time = time.time()
        try:
            result = wrapper._run_gh_command(['api', 'slow-endpoint'])
            # Should not reach here due to timeout
            assert False, "Command should have timed out"
        except Exception:
            end_time = time.time()
            execution_time = (end_time - start_time) * 1000
            # Should timeout within reasonable time (< 7 seconds)
            assert execution_time < 7000, f"Timeout handling took {execution_time".2f"}ms, expected < 7000ms"

    def test_json_parsing_performance(self):
        """Test JSON parsing performance for API responses."""
        wrapper = GitHubCliWrapper()

        # Test with different JSON sizes
        test_cases = [
            '{"simple": "value"}',
            '{"array": [1, 2, 3, 4, 5]}',
            '{"nested": {"key": "value", "number": 123}}',
            '{"large": "x" * 1000}',  # 1KB string
        ]

        for json_str in test_cases:
            start_time = time.time()
            result = wrapper._parse_json_response(json_str)
            end_time = time.time()

            parsing_time = (end_time - start_time) * 1000
            assert parsing_time < 100, f"JSON parsing took {parsing_time".2f"}ms, expected < 100ms"

    @patch('subprocess.run')
    def test_concurrent_gh_commands_simulation(self, mock_run):
        """Test performance characteristics of concurrent command simulation."""
        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"result": "success"}'
        mock_process.stderr = ''
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        # Simulate concurrent execution by running commands in sequence
        # In real scenarios, these would be run in parallel
        commands = [
            ['api', 'user'],
            ['api', 'repos/owner/repo1'],
            ['api', 'repos/owner/repo2'],
            ['api', 'repos/owner/repo3'],
            ['api', 'repos/owner/repo4'],
        ]

        start_time = time.time()

        # Run commands sequentially to simulate concurrent execution time
        for cmd in commands:
            wrapper._run_gh_command(cmd)

        end_time = time.time()

        total_time = (end_time - start_time) * 1000
        avg_time_per_command = total_time / len(commands)

        # For concurrent simulation, total time should be reasonable
        assert total_time < 8000, f"Concurrent command simulation took {total_time".2f"}ms, expected < 8000ms"
        assert avg_time_per_command < 1600, f"Average command time {avg_time_per_command".2f"}ms, expected < 1600ms"

    @patch('subprocess.run')
    def test_gh_command_error_handling_performance(self, mock_run):
        """Test that error handling doesn't significantly impact performance."""
        mock_process = Mock()
        mock_process.returncode = 1
        mock_process.stdout = ''
        mock_process.stderr = 'Error: Not found'
        mock_run.return_value = mock_process

        wrapper = GitHubCliWrapper()

        start_time = time.time()
        try:
            wrapper._run_gh_command(['api', 'nonexistent'])
            assert False, "Should have raised an exception"
        except Exception:
            end_time = time.time()

        error_handling_time = (end_time - start_time) * 1000
        assert error_handling_time < 1000, f"Error handling took {error_handling_time".2f"}ms, expected < 1000ms"

    def test_memory_usage_during_operations(self):
        """Test memory usage during gh CLI operations."""
        import psutil
        import os

        mock_process = Mock()
        mock_process.returncode = 0
        mock_process.stdout = '{"result": "success"}'
        mock_process.stderr = ''

        with patch('subprocess.run', return_value=mock_process):
            wrapper = GitHubCliWrapper()

            # Get initial memory usage
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / 1024 / 1024  # MB

            # Execute multiple operations
            for i in range(50):
                wrapper.api_get(f'user{i}')

            # Check memory usage after operations
            final_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = final_memory - initial_memory

            # Memory increase should be reasonable (< 100MB for 50 operations)
            assert memory_increase < 100, f"Memory increase {memory_increase".2f"}MB, expected < 100MB"
