import csv
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from measurement import offline_max2_parallel_measurement as measurement
from measurement.device_backends import LegacyBackend


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.files = measurement.make_run_files(self.path)
        Path(self.files.metadata_json).write_text("{}")
        self.shared = SimpleNamespace(start_event=threading.Event(), stop_event=threading.Event(),
            serial_ready_event=threading.Event(), phase_code=SimpleNamespace(value=2),
            frame_index=SimpleNamespace(value=8), frame_time_ns=SimpleNamespace(value=123),
            visual_start_ns=SimpleNamespace(value=1), stimulus_active=SimpleNamespace(value=1),
            stimulus_on_mask=SimpleNamespace(value=1))
        self.shared.start_event.set()
        self.config = SimpleNamespace(com_port="FAKE", baudrate=115200, serial_timeout_sec=.001,
            serial_warmup_sec=0, device_type="legacy", channel_mode="auto")
        self.errors = queue.Queue()

    def run_worker(self, backend):
        port = Mock()
        port.__enter__ = Mock(return_value=port)
        port.__exit__ = Mock(return_value=False)
        with patch("serial.Serial", return_value=port), patch.object(measurement, "set_realtime_priority"), \
             patch.dict(measurement.BACKENDS, {"legacy": lambda ser, config: backend}):
            measurement.serial_worker(self.config, self.shared, self.files, self.errors)
        port.__exit__.assert_called_once()

    def test_stop_on_prepare_start_and_read_failure(self):
        for failing in ("prepare", "start", "read"):
            with self.subTest(failing=failing):
                self.shared.stop_event.clear()
                backend = Mock()
                backend.csv_fields = LegacyBackend.csv_fields
                backend.device_info.return_value = {"type": "legacy"}
                backend.statistics.return_value = {}
                backend.stop.return_value = []
                getattr(backend, failing).side_effect = RuntimeError("disconnected")
                self.run_worker(backend)
                backend.stop.assert_called_once()
                self.assertTrue(self.shared.stop_event.is_set())

    def test_cancel_while_waiting_for_visual(self):
        self.shared.start_event.clear()
        backend = Mock()
        backend.csv_fields = LegacyBackend.csv_fields
        backend.prepare.side_effect = lambda stop: stop.set()
        backend.device_info.return_value = {"type": "legacy"}
        backend.statistics.return_value = {}
        backend.stop.return_value = []
        self.run_worker(backend)
        backend.stop.assert_called_once()
        backend.start.assert_not_called()

    def test_legacy_csv_header_and_values(self):
        port = Mock()
        raw = iter([b"1,2,3\r\n", b"invalid\r\n"])
        def readline():
            try:
                return next(raw)
            except StopIteration:
                self.shared.stop_event.set()
                return b""
        port.readline.side_effect = readline
        backend = LegacyBackend(port, self.config)
        self.run_worker(backend)
        with open(self.files.serial_csv, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["sample_index", "pc_time_ns", "experiment_time_s", "phase_code",
            "phase_name", "frame_index", "frame_time_ns", "stimulus_active", "stimulus_on_mask",
            "serial_channel_count", "ch1", "ch2", "ch3", "parse_error", "raw_line"])
        self.assertEqual(rows[1][9:], ["3", "1.0", "2.0", "3.0", "", "1,2,3"])
        self.assertTrue(rows[2][-2])
        port.write.assert_not_called()
