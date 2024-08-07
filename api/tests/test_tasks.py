from unittest.mock import patch, MagicMock

from django.conf import settings
from django.test import TestCase

from api.models import TestEnvironment, TestRunRequest, TestFilePath
from api.tasks import handle_task_retry, MAX_RETRY, execute_test_run_request


class TestTasks(TestCase):

    def setUp(self) -> None:
        self.env = TestEnvironment.objects.create(name='my_env')
        self.test_run_req = TestRunRequest.objects.create(requested_by='Ramadan', env=self.env)
        self.path1 = TestFilePath.objects.create(path='path1')
        self.path2 = TestFilePath.objects.create(path='path2')
        self.test_run_req.path.add(self.path1)
        self.test_run_req.path.add(self.path2)

    @patch('api.tasks.execute_test_run_request.s')
    def test_handle_task_retry_less_than_max_retry(self, task_mock):
        handle_task_retry(self.test_run_req, MAX_RETRY - 1)
        self.assertEqual(TestRunRequest.StatusChoices.RETRYING.name, self.test_run_req.status)
        self.assertEqual(f'\nFailed to run tests on env my_env retrying in 512 seconds.', self.test_run_req.logs)
        self.assertTrue(task_mock.called)
        task_mock.assert_called_with(self.test_run_req.id, MAX_RETRY)

    def test_handle_task_retry_equal_to_max_retry(self):
        handle_task_retry(self.test_run_req, MAX_RETRY)
        self.assertEqual(TestRunRequest.StatusChoices.FAILED_TO_START.name, self.test_run_req.status)
        self.assertEqual(f'\nFailed to run tests on env my_env after retrying 10 times.', self.test_run_req.logs)

    @patch('api.tasks.handle_task_retry')
    def test_execute_test_run_request_busy_env(self, retry):
        self.env.status = TestEnvironment.StatusChoices.BUSY.name
        self.env.save()
        execute_test_run_request(self.test_run_req.id)
        self.assertTrue(retry.called)
        retry.assert_called_with(self.test_run_req, 0)

    @patch('subprocess.Popen.wait', return_value=1)
    def test_execute_test_run_request_failed(self, wait):
        execute_test_run_request(self.test_run_req.id)
        self.test_run_req.refresh_from_db()
        self.assertTrue(wait.called)
        wait.assert_called_with(timeout=settings.TEST_RUN_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(TestRunRequest.StatusChoices.FAILED.name, self.test_run_req.status)

    @patch('subprocess.Popen.wait', return_value=0)
    def test_execute_test_run_request_success(self, wait):
        execute_test_run_request(self.test_run_req.id)
        self.test_run_req.refresh_from_db()
        self.assertTrue(wait.called)
        wait.assert_called_with(timeout=settings.TEST_RUN_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(TestRunRequest.StatusChoices.SUCCESS.name, self.test_run_req.status)


class TestEnvironmentLocking(TestCase):

    def setUp(self):
        self.env = TestEnvironment.objects.create(name='test_env')
        self.test_run_request = TestRunRequest.objects.create(env=self.env)

    @patch('api.tasks.subprocess.Popen')
    def test_environment_locking(self, mock_popen):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stdout.read.return_value = 'Test output'
        mock_popen.return_value = mock_process

        with patch('api.models.TestEnvironment.lock') as mock_lock, \
                patch('api.models.TestEnvironment.unlock') as mock_unlock, \
                patch('api.models.TestEnvironment.is_busy', return_value=False), \
                patch('api.models.TestRunRequest.save_logs') as mock_save_logs:
            execute_test_run_request(self.test_run_request.id)

            # Ensure the environment was locked and then unlocked
            mock_lock.assert_called_once()
            mock_unlock.assert_called_once()

            # Ensure logs were saved
            mock_save_logs.assert_called_with(logs='Test output')

    @patch('api.tasks.handle_task_retry')
    def test_environment_busy_retries(self, mock_handle_task_retry):
        with patch('api.models.TestEnvironment.is_busy', return_value=True):
            execute_test_run_request(self.test_run_request.id)

            # Ensure that the retry handler was called
            mock_handle_task_retry.assert_called_once_with(self.test_run_request, 0)

    @patch('api.tasks.subprocess.Popen')
    def test_environment_unlocking_after_command(self, mock_popen):
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.stdout.read.return_value = 'Test output'
        mock_popen.return_value = mock_process

        with patch('api.models.TestEnvironment.lock') as mock_lock, \
                patch('api.models.TestEnvironment.unlock') as mock_unlock, \
                patch('api.models.TestEnvironment.is_busy', return_value=False), \
                patch('api.models.TestRunRequest.save_logs') as mock_save_logs:
            execute_test_run_request(self.test_run_request.id)

            # Ensure the environment was locked and then unlocked
            mock_lock.assert_called_once()
            mock_unlock.assert_called_once()

            # Ensure logs were saved
            mock_save_logs.assert_called_with(logs='Test output')

    @patch('api.tasks.subprocess.Popen')
    def test_environment_unlocked_on_failure(self, mock_popen):
        mock_process = MagicMock()
        mock_process.wait.return_value = 1  # Simulate a command failure
        mock_process.stdout.read.return_value = 'Test output'
        mock_popen.return_value = mock_process

        with patch('api.models.TestEnvironment.lock') as mock_lock, \
                patch('api.models.TestEnvironment.unlock') as mock_unlock, \
                patch('api.models.TestEnvironment.is_busy', return_value=False), \
                patch('api.models.TestRunRequest.save_logs') as mock_save_logs:
            execute_test_run_request(self.test_run_request.id)

            # Ensure the environment was locked and then unlocked
            mock_lock.assert_called_once()
            mock_unlock.assert_called_once()

            # Ensure logs were saved
            mock_save_logs.assert_called_with(logs='Test output')
