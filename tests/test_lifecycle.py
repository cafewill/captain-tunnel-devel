"""실제 SSH 없이 경합 순서를 Event로 고정해 재현한다."""

import os
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from captain_tunnel import application as app


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = app.Engine(queue.Queue())
        self.engine.server = {"host": "old", "port": 22, "user": "tester", "auth": "password"}
        self.engine.password = "old-password"
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def run_pending_connection(self, action, failure=None):
        entered, release = threading.Event(), threading.Event()
        client = Mock()

        def connect(**kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test barrier timed out")
            if failure:
                raise failure

        client.connect.side_effect = connect
        errors = []

        def work():
            try:
                self.engine._connect()
            except Exception as exc:
                errors.append(exc)

        with patch.object(app.paramiko, "SSHClient", return_value=client), \
             patch.object(app, "KNOWN_HOSTS", os.path.join(self.temp.name, "known_hosts")), \
             patch.object(app, "USER_KNOWN_HOSTS", os.path.join(self.temp.name, "missing")):
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                action()
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
        return client, errors

    def test_stop_discards_late_success(self):
        client, errors = self.run_pending_connection(self.engine.stop)
        self.assertIsNone(self.engine.transport)
        self.assertFalse(self.engine.connected.is_set())
        client.close.assert_called()
        self.assertIsInstance(errors[0], app.ConnectionAttemptCancelled)

    def test_restart_cannot_publish_old_server(self):
        def restart():
            self.engine.stop()
            # supervisor를 만들지 않고 새 설정 start()의 동일 경로를 실행한다.
            supervisor = Mock()
            supervisor.is_alive.return_value = True
            self.engine.thread = supervisor
            self.engine.start({**self.engine.server, "host": "new"}, "new-password")

        _, errors = self.run_pending_connection(restart)
        self.assertEqual(self.engine.server["host"], "new")
        self.assertTrue(self.engine.want)
        self.assertIsNone(self.engine.transport)
        self.assertIsInstance(errors[0], app.ConnectionAttemptCancelled)

    def test_late_auth_failure_becomes_cancellation(self):
        _, errors = self.run_pending_connection(self.engine.stop, app.paramiko.AuthenticationException("old"))
        self.assertIsInstance(errors[0], app.ConnectionAttemptCancelled)

    def test_old_keepalive_cannot_refresh_new_connection(self):
        old = Mock()
        self.engine.transport = Mock()
        self.engine.last_ka_ok = 123
        self.engine._keepalive_probe(old)
        self.assertEqual(self.engine.last_ka_ok, 123)
        self.assertFalse(self.engine.ka_replied)

    def test_cancelled_hostkey_prompt_does_not_wait_or_enqueue(self):
        generation = self.engine._generation
        self.engine.stop()
        while not self.engine.events.empty():
            self.engine.events.get_nowait()
        self.assertFalse(self.engine.ask_hostkey("old", "ssh-rsa", "fingerprint", generation=generation))
        self.assertTrue(self.engine.events.empty())

    def test_stopped_forwarder_does_not_open_channel(self):
        engine, connection = Mock(), Mock()
        forwarder = app.Forwarder(engine, {"name": "test", "local_port": 18080})
        forwarder.stop()
        forwarder._handle(connection, ("127.0.0.1", 1234))
        engine.open_channel.assert_not_called()
        connection.close.assert_called_once()

    def test_pending_forwarder_is_closed_after_stop(self):
        connection, channel, engine = Mock(), Mock(), Mock()
        forwarder = app.Forwarder(engine, {
            "name": "test", "local_port": 18080, "remote_host": "remote", "remote_port": 8080
        })

        def open_channel(*args):
            forwarder.stop()
            return channel, None

        engine.open_channel.side_effect = open_channel
        forwarder._handle(connection, ("127.0.0.1", 1234))
        connection.close.assert_called_once()
        channel.close.assert_called_once()
        self.assertEqual(forwarder.active, 0)
