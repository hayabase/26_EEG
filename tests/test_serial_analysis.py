import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from analysis import FFT, Wavelet, PhaseTiming
from analysis.serial_data import resolve_channels, read_sample_rate


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "serial_samples.csv"

    def write(self, channels, eeg26=False):
        fields = ["experiment_time_s", "phase_name", "parse_error", *channels]
        if eeg26:
            fields += ["device_time_unwrapped_us", "crc_ok"]
            (self.path.parent / "metadata.json").write_text(json.dumps({
                "device": {"type": "eeg26", "sample_rate_hz": 1000}}))
        with self.path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fields)
            writer.writeheader()
            for i in range(1000):
                row = {"experiment_time_s": i / 1000 if not eeg26 else (i // 7) * .007,
                       "phase_name": "stimulus", "parse_error": ""}
                row.update({c: np.sin(2 * np.pi * 10 * i / 1000) for c in channels})
                if eeg26:
                    row.update(device_time_unwrapped_us=0x100000000 + i * 1000, crc_ok=1)
                writer.writerow(row)

    def test_legacy_header_and_unchanged_time(self):
        self.write(("ch1", "ch2", "ch3"))
        self.assertEqual(resolve_channels(self.path), ("ch1", "ch2", "ch3"))
        self.assertIsNone(read_sample_rate(self.path))
        for module in (FFT, Wavelet, PhaseTiming):
            series = (module.load_channel_series(self.path, ("ch1",)) if module is PhaseTiming else
                      module.load_channel_series(self.path, "all", ("ch1",)))
            np.testing.assert_array_equal(series["ch1"][0], np.arange(1000) / 1000)

    def test_all_16_and_batched_pc_time(self):
        channels = tuple(f"ch{i}" for i in range(1, 17))
        self.write(channels, eeg26=True)
        self.assertEqual(resolve_channels(self.path), channels)
        self.assertEqual(read_sample_rate(self.path), 1000)
        for module in (FFT, Wavelet, PhaseTiming):
            series = (module.load_channel_series(self.path, channels) if module is PhaseTiming else
                      module.load_channel_series(self.path, "all", channels))
            self.assertEqual(len(series), 16)
            np.testing.assert_allclose(series["ch16"][0], np.arange(1000) / 1000, atol=1e-12)
        t, v = series["ch16"]
        result = FFT.compute_fft("ch16", t, v, .5, 60, 10, 1000)
        self.assertAlmostEqual(result.peak_frequency_hz, 10)
        self.assertEqual(result.sample_rate_hz, 1000)

    def test_missing_channel_is_clear_error(self):
        self.write(("ch1",))
        with self.assertRaisesRegex(ValueError, "not present"):
            resolve_channels(self.path, ("ch16",))

    def test_legacy_channels_keep_independent_rate_estimates(self):
        series = {"ch1": (np.arange(10) / 1000, np.arange(10)),
                  "ch2": (np.arange(10) / 500, np.arange(10))}
        result = PhaseTiming.build_uniform_channels(series, np.array([1.]), np.array([1.]), False, 0)
        self.assertAlmostEqual(result["ch1"].sample_rate_hz, 1000)
        self.assertAlmostEqual(result["ch2"].sample_rate_hz, 500)
