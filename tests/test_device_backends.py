import struct
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from measurement.device_backends import (EEG26FrameParser, EEG26Backend, LegacyBackend,
    crc16_ccitt_false, parse_serial_line)


def frame(sequence=1, timestamp=1000, values=None, version=2, length=54):
    values = values or [0, 0x7FFFFF, -0x800000, -1] * 4
    payload = b"".join(b"\xc0\x00\x00" + b"".join((v & 0xFFFFFF).to_bytes(3, "big")
                         for v in values[chip * 8:(chip + 1) * 8]) for chip in range(2))
    data = b"\xa5\x5a" + bytes([version, length]) + struct.pack("<II", sequence, timestamp) + payload
    return data + crc16_ccitt_false(data).to_bytes(2, "big")


class ParserTests(unittest.TestCase):
    def test_crc_reference(self):
        # Standard CCITT-FALSE check vector, independent of frame generator.
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_normal_and_all_signed_channels(self):
        data = frame()
        self.assertEqual(len(data), 68)
        sample = EEG26FrameParser().feed(data)[0]
        self.assertEqual(sample.channels, tuple([0, 8388607, -8388608, -1] * 4))
        self.assertEqual((sample.sequence, sample.device_time_us), (1, 1000))

    def test_concatenated(self):
        parser = EEG26FrameParser()
        self.assertEqual(len(parser.feed(frame() + frame(2, 2000))), 2)

    def test_every_split(self):
        for split in range(1, 68):
            parser = EEG26FrameParser()
            self.assertEqual(parser.feed(frame()[:split]), [])
            self.assertEqual(len(parser.feed(frame()[split:])), 1)

    def test_garbage_and_split_sync(self):
        parser = EEG26FrameParser()
        self.assertEqual(parser.feed(b"garbage\xa5"), [])
        self.assertEqual(len(parser.feed(frame()[1:])), 1)

    def test_crc_recovery(self):
        parser = EEG26FrameParser()
        damaged = bytearray(frame())
        damaged[30] ^= 1
        samples = parser.feed(bytes(damaged) + frame(2))
        self.assertEqual(len(samples), 1)
        self.assertEqual(parser.crc_errors, 1)

    def test_inserted_byte_recovers_overlapping_frame(self):
        parser = EEG26FrameParser()
        samples = parser.feed(frame()[:22] + b"noise" + frame(2))
        self.assertEqual([s.sequence for s in samples], [2])

    def test_false_sync_header_inside_corruption(self):
        parser = EEG26FrameParser()
        samples = parser.feed(b"\xa5\x5a\x99\x99" + b"garbage" * 10 + frame(2))
        self.assertEqual([s.sequence for s in samples], [2])

    def test_sequence_gap(self):
        parser = EEG26FrameParser()
        samples = parser.feed(frame() + frame(4, 4000))
        self.assertEqual(samples[1].sequence_gap, 2)
        self.assertEqual(parser.sequence_gaps, 2)

    def test_wraps(self):
        parser = EEG26FrameParser()
        samples = parser.feed(frame(0xFFFFFFFF, 0xFFFFFF00) + frame(0, 0x100))
        self.assertEqual(samples[1].sequence_gap, 0)
        self.assertEqual(samples[1].device_time_unwrapped_us, 0x100000100)

    def test_unsupported_version(self):
        with self.assertRaisesRegex(ValueError, "Unsupported.*version 1"):
            EEG26FrameParser().feed(frame(version=1))

    def test_invalid_length(self):
        with self.assertRaisesRegex(ValueError, "payload length"):
            EEG26FrameParser().feed(frame(length=53))

    def test_bounded_garbage_and_partial_frame(self):
        parser = EEG26FrameParser()
        parser.feed(b"x" * 100000)
        self.assertEqual(len(parser.buffer), 0)
        parser.feed(frame()[:40])
        self.assertEqual(parser.statistics()["partial_frame_bytes"], 40)


class CompatibilityTests(unittest.TestCase):
    def test_legacy_parse(self):
        self.assertEqual(parse_serial_line(b"12\r\n", "auto"), ([12.0, None, None], 1, "12"))
        self.assertEqual(parse_serial_line(b"1,2,3,4\r\n", "auto")[0], [1., 2., 3.])
        with self.assertRaises(ValueError):
            parse_serial_line(b"1,2", "auto")

    def test_legacy_no_commands_and_error_row(self):
        ser = Mock()
        ser.readline.return_value = b"bad\r\n"
        backend = LegacyBackend(ser, SimpleNamespace(channel_mode="auto"))
        backend.start(); backend.stop()
        self.assertTrue(backend.read()[0][1][-2])
        ser.write.assert_not_called()


class CommandTests(unittest.TestCase):
    def backend(self, response):
        ser = Mock()
        ser.read.side_effect = [bytes([b]) for b in response]
        return EEG26Backend(ser, SimpleNamespace(sample_rate_hz=1000)), ser

    def test_start_ack_does_not_eat_frame(self):
        backend, ser = self.backend(b"STARTED EEG\r\n" + frame())
        backend.start()
        self.assertEqual(ser.read.call_count, len(b"STARTED EEG\r\n"))

    def test_error_prevents_start(self):
        for reply in (b"ERR configuration\r\n", b"WREG result=MISMATCH/ERROR\r\n"):
            backend, ser = self.backend(reply)
            with self.assertRaises(RuntimeError):
                backend.command("MODE EEG", "DONE")
            self.assertNotIn(unittest.mock.call(b"START\r\n"), ser.write.call_args_list)

    def test_stop_flush_preserves_last_frames(self):
        backend, _ = self.backend(frame() + b"STOPPED sequence=1 dropped=0\r\n")
        self.assertEqual(len(backend.stop()), 1)
        self.assertTrue(backend.statistics()["stop_acknowledged"])

    def test_acknowledgement_timeout(self):
        backend, ser = self.backend(b"")
        ser.read.side_effect = None
        ser.read.return_value = b""
        with self.assertRaises(TimeoutError):
            backend.command("START", "STARTED EEG", timeout=.001)

    def test_verified_rate_and_initialization_order(self):
        for requested, expected in ((None, 250), (1000, 1000), (8000, 8000)):
            backend = EEG26Backend(Mock(), SimpleNamespace(sample_rate_hz=requested, serial_warmup_sec=0))
            # Simulate a firmware default of 500 SPS: PC must override it even
            # when the CLI sample-rate option was omitted.
            configs = [0xD5, 0xD5]
            calls = []
            def command(text, terminal, **kwargs):
                calls.append(text)
                if text == "STOP": return b"", "STOPPED sequence=0 dropped=0"
                if text == "STATUS": return "STATUS protocol=2 timebase=1000000Hz"
                if text == "MODE EEG": return "OK configuration readback MATCH"
                if text.startswith("WREG"):
                    parts = text.split(); configs[int(parts[1]) - 1] = int(parts[3], 16)
                    return "MATCH"
                if text.startswith("RREG"):
                    return f"[0x01] = 0x{configs[int(text.split()[1]) - 1]:02X}"
                return "OK FORMAT BINARY"
            backend.command = command
            stop = Mock(); stop.wait.return_value = False
            backend.prepare(stop)
            self.assertEqual(backend.sample_rate_hz, expected)
            self.assertEqual(calls[:3], ["STOP", "STATUS", "MODE EEG"])
            self.assertEqual(calls[-1], "FORMAT BINARY")
            target = {250: "D6", 1000: "D4", 8000: "D1"}[expected]
            self.assertIn(f"WREG 1 01 {target}", calls)
            self.assertIn(f"WREG 2 01 {target}", calls)
            self.assertEqual(backend.device_info()["sample_rate_hz"], expected)

    def test_unsupported_rate_sends_no_commands(self):
        port = Mock()
        backend = EEG26Backend(port, SimpleNamespace(sample_rate_hz=16000, serial_warmup_sec=0))
        with self.assertRaisesRegex(ValueError, "Unsupported EEG26 sample rate 16000"):
            backend.prepare(Mock())
        port.write.assert_not_called()

    def test_cli_rejects_16000(self):
        from measurement.offline_max2_parallel_measurement import parse_args
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            parse_args(["--device", "eeg26", "--sample-rate", "16000"])
        self.assertEqual(result.exception.code, 2)

    def test_timestamp_gap_without_sequence_gap(self):
        backend, _ = self.backend(b"")
        backend.sample_rate_hz = 1000
        backend.decode(frame(1, 1000) + frame(2, 3000))
        self.assertEqual(backend.statistics()["sequence_gaps"], 0)
        self.assertEqual(backend.statistics()["timestamp_gap_events"], 1)


if __name__ == "__main__":
    unittest.main()
