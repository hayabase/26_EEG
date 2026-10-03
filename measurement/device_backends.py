"""Device-specific transport; timestamps in this module never replace PC clocks."""
from __future__ import annotations

import binascii
import re
import struct
import time
from dataclasses import dataclass

SYNC = b"\xa5\x5a"
PROTOCOL_VERSION = 2
PAYLOAD_BYTES = 54
FRAME_BYTES = 68
CHANNEL_COUNT = 16
CHANNELS = tuple(f"ch{i}" for i in range(1, CHANNEL_COUNT + 1))
UINT32_MASK = 0xFFFFFFFF
# ADS1299 CONFIG1 DR[2:0], with the firmware's external 2.048 MHz clock.
# EEG26's supported operating range ends at 8000 SPS; exclude DR=0 (16000).
RATE_CODES = {8000: 1, 4000: 2, 2000: 3, 1000: 4, 500: 5, 250: 6}
# PC-owned experiment default. Always written after MODE EEG resets the ADS chips.
DEFAULT_EEG26_SAMPLE_RATE_HZ = 250


def crc16_ccitt_false(data: bytes) -> int:
    # Polynomial 0x1021, initial 0xffff, no reflection, no final XOR.
    return binascii.crc_hqx(data, 0xFFFF)


def decode24(data: bytes) -> int:
    return int.from_bytes(data, "big", signed=True)


@dataclass(frozen=True)
class EEG26Sample:
    sequence: int
    device_time_us: int
    device_time_unwrapped_us: int
    channels: tuple[int, ...]
    sequence_gap: int


class EEG26FrameParser:
    def __init__(self):
        self.buffer = bytearray()
        self.received_frames = 0
        self.crc_errors = 0
        self.sequence_gaps = 0
        self.sequence_gap_events = 0
        self.discarded_bytes = 0
        self.previous_sequence = None
        self.previous_time = None
        self.unwrapped_time = 0

    def feed(self, data: bytes) -> list[EEG26Sample]:
        self.buffer.extend(data)
        samples = []
        while True:
            start = self.buffer.find(SYNC)
            if start < 0:
                keep = int(self.buffer.endswith(SYNC[:1]))
                self.discarded_bytes += len(self.buffer) - keep
                if keep:
                    self.buffer[:] = self.buffer[-1:]
                else:
                    self.buffer.clear()
                break
            self.discarded_bytes += start
            del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            if len(self.buffer) < FRAME_BYTES:
                break
            frame = bytes(self.buffer[:FRAME_BYTES])
            if crc16_ccitt_false(frame[:-2]) != int.from_bytes(frame[-2:], "big"):
                self.crc_errors += 1
                self.discarded_bytes += 1
                del self.buffer[0]  # Search again, including sync embedded in the damaged frame.
                continue
            # Validate the CRC before trusting a header found during resynchronization.
            if frame[2] != PROTOCOL_VERSION:
                raise ValueError(f"Unsupported EEG26 protocol version {frame[2]}; expected {PROTOCOL_VERSION}")
            if frame[3] != PAYLOAD_BYTES:
                raise ValueError(f"Invalid EEG26 ADS payload length {frame[3]}; expected {PAYLOAD_BYTES}")
            sequence, timestamp = struct.unpack_from("<II", frame, 4)
            gap = 0
            if self.previous_sequence is not None:
                delta = (sequence - self.previous_sequence) & UINT32_MASK
                if delta == 0 or delta >= 0x80000000:
                    raise ValueError("EEG26 sequence duplicate/reset/out-of-order")
                gap = delta - 1
            if self.previous_time is None:
                self.unwrapped_time = timestamp
            else:
                delta_us = (timestamp - self.previous_time) & UINT32_MASK
                if delta_us >= 0x80000000:
                    raise ValueError("EEG26 device timestamp reset/out-of-order")
                self.unwrapped_time += delta_us
            values = tuple(decode24(frame[12 + (ch // 8) * 27 + 3 + (ch % 8) * 3:
                                           12 + (ch // 8) * 27 + 6 + (ch % 8) * 3])
                           for ch in range(CHANNEL_COUNT))
            samples.append(EEG26Sample(sequence, timestamp, self.unwrapped_time, values, gap))
            self.previous_sequence, self.previous_time = sequence, timestamp
            self.received_frames += 1
            self.sequence_gaps += gap
            self.sequence_gap_events += int(gap > 0)
            del self.buffer[:FRAME_BYTES]
        return samples

    def statistics(self):
        return {name: getattr(self, name) for name in
                ("received_frames", "crc_errors", "sequence_gaps", "sequence_gap_events", "discarded_bytes")} | {
                    "partial_frame_bytes": len(self.buffer)}


def parse_serial_line(raw_line: bytes, channel_mode: str):
    text = raw_line.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("empty line")
    parts = [part.strip() for part in text.split(",")]
    if channel_mode == "one":
        parts = parts[:1]
    elif channel_mode == "three":
        if len(parts) < 3:
            raise ValueError(f"expected 3 channels, got {len(parts)}")
        parts = parts[:3]
    elif channel_mode == "auto":
        if len(parts) >= 3:
            parts = parts[:3]
        elif len(parts) == 1:
            parts = parts[:1]
        else:
            raise ValueError(f"expected 1 or 3 channels, got {len(parts)}")
    else:
        raise ValueError(f"unknown channel mode: {channel_mode}")
    values = [float(part) for part in parts]
    count = len(values)
    values.extend([None] * (3 - count))
    return values, count, text


class LegacyBackend:
    csv_fields = ("serial_channel_count", "ch1", "ch2", "ch3", "parse_error", "raw_line")

    def __init__(self, serial_port, config):
        self.ser, self.config = serial_port, config

    def prepare(self, stop_event):
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        if self.config.serial_warmup_sec > 0:
            print(f"serial warmup: discarding data for {self.config.serial_warmup_sec:.3f} sec")
        end = time.perf_counter() + self.config.serial_warmup_sec
        while time.perf_counter() < end and not stop_event.is_set():
            self.ser.readline()
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def start(self):
        pass

    def read(self):
        raw = self.ser.readline()
        if not raw:
            return []
        now = time.perf_counter_ns()
        values, count, error = [None] * 3, "", ""
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            values, count, text = parse_serial_line(raw, self.config.channel_mode)
        except Exception as exc:
            error = str(exc)
        return [(now, [count, *("" if v is None else v for v in values), error, text])]

    def stop(self):
        return []

    def device_info(self):
        return {"type": "legacy", "channel_count": "auto" if self.config.channel_mode == "auto" else
                {"one": 1, "three": 3}[self.config.channel_mode], "sample_rate_hz": None}

    def statistics(self):
        return {}


class EEG26Backend:
    csv_fields = ("serial_channel_count", "sequence", "device_time_us", "device_time_unwrapped_us",
                  "protocol_version", *CHANNELS, "crc_ok", "sequence_gap", "parse_error")

    def __init__(self, serial_port, config):
        self.ser, self.config = serial_port, config
        self.parser = EEG26FrameParser()
        self.sample_rate_hz = None
        self.config1 = []
        self.command_log = []
        self.stop_reply = None
        self.last_data_time = None
        self.serial_timeouts = 0
        self.first_device_time = None
        self.last_device_time = None
        self.timestamp_gap_events = 0
        self.max_sample_interval_us = 0

    def command(self, command: str, terminal: str, timeout=3.0, binary_prefix=False):
        """Read only through the acknowledgement newline; never consume START payload."""
        self.ser.write((command + "\r\n").encode("ascii"))
        response = bytearray()
        deadline = time.perf_counter() + timeout
        pattern = re.compile(terminal.encode("ascii"))
        while time.perf_counter() < deadline:
            byte = self.ser.read(1)
            if not byte:
                continue
            response.extend(byte)
            if byte != b"\n":
                continue
            line = bytes(response).rsplit(b"\n", 2)[-2].strip(b"\r")
            if not binary_prefix and (b"ERR" in line or b"MISMATCH" in line or b"ERROR" in line):
                raise RuntimeError(f"{command}: {line.decode('ascii', errors='replace')}")
            match = pattern.search(bytes(response))
            if match:
                text = bytes(response[match.start():]).decode("ascii", errors="replace").strip()
                self.command_log.append({"command": command, "response": text if binary_prefix else
                                         bytes(response).decode("ascii", errors="replace").strip()})
                if binary_prefix:
                    return bytes(response[:match.start()]), text
                return bytes(response).decode("ascii", errors="replace")
            if len(response) > 1024 * 1024:
                raise RuntimeError(f"{command}: response too large")
        raise TimeoutError(f"{command}: acknowledgement timeout")

    def prepare(self, stop_event):
        # Opening CDC does not reset this board; allow its USB startup banner to finish.
        requested = (self.config.sample_rate_hz if self.config.sample_rate_hz is not None
                     else DEFAULT_EEG26_SAMPLE_RATE_HZ)
        if requested not in RATE_CODES:
            raise ValueError(f"Unsupported EEG26 sample rate {requested}; supported rates: {sorted(RATE_CODES)} SPS")
        if stop_event.wait(max(1.1, self.config.serial_warmup_sec)):
            return
        self.command("STOP", r"STOPPED sequence=\d+ dropped=\d+\r\n", binary_prefix=True)
        status = self.command("STATUS", r"STATUS [^\r\n]+\r\n")
        if "protocol=2" not in status or "timebase=1000000Hz" not in status:
            raise RuntimeError(f"Unsupported EEG26 STATUS: {status.strip()}")
        reply = self.command("MODE EEG", r"Send register changes if needed, then START\.\r\n")
        if "OK configuration readback MATCH" not in reply:
            raise RuntimeError("MODE EEG configuration was not verified")
        initial = [self.read_config1(device) for device in (1, 2)]
        for device, value in enumerate(initial, 1):
            target = (value & ~7) | RATE_CODES[requested]
            self.command(f"WREG {device} 01 {target:02X}",
                         rf"\[0x01\] wrote=0x{target:02X} read=0x{target:02X} MATCH\r\n")
        self.config1 = [self.read_config1(device) for device in (1, 2)]
        rates = [{v: k for k, v in RATE_CODES.items()}.get(value & 7) for value in self.config1]
        if rates[0] is None or rates[0] != rates[1] or rates[0] != requested:
            raise RuntimeError(f"ADS1299 sample rate readback mismatch: {rates}, requested={requested}")
        if any((value & 0xC0) != 0xC0 for value in self.config1):
            raise RuntimeError("Unexpected ADS1299 clock/multiple-readback setting")
        self.sample_rate_hz = rates[0]
        self.command("FORMAT BINARY", r"OK FORMAT BINARY\r\n")

    def read_config1(self, device):
        reply = self.command(f"RREG {device} 01 01", r"\[0x01\] = 0x[0-9A-F]{2}\r\n")
        return int(re.search(r"\[0x01\] = 0x([0-9A-F]{2})", reply)[1], 16)

    def start(self):
        self.command("START", r"STARTED EEG\r\n")
        self.last_data_time = time.perf_counter()

    def decode(self, data):
        now = time.perf_counter_ns()
        samples = self.parser.feed(data)
        for sample in samples:
            if self.first_device_time is None:
                self.first_device_time = sample.device_time_unwrapped_us
            if self.last_device_time is not None:
                interval = sample.device_time_unwrapped_us - self.last_device_time
                self.max_sample_interval_us = max(self.max_sample_interval_us, interval)
                if self.sample_rate_hz and interval > 1.5e6 / self.sample_rate_hz:
                    self.timestamp_gap_events += 1
            self.last_device_time = sample.device_time_unwrapped_us
        return [(now, [CHANNEL_COUNT, s.sequence, s.device_time_us, s.device_time_unwrapped_us,
                       PROTOCOL_VERSION, *s.channels, 1, s.sequence_gap, ""])
                for s in samples]

    def read(self):
        data = self.ser.read(min(self.ser.in_waiting or 1, 65536))
        if not data:
            self.serial_timeouts += 1
        samples = self.decode(data)
        if samples:
            self.last_data_time = time.perf_counter()
        if time.perf_counter() - self.last_data_time > 3.0:
            raise TimeoutError("EEG26: no valid frames for 3 seconds (timeout/disconnection)")
        return samples

    def stop(self):
        prefix, self.stop_reply = self.command("STOP", r"STOPPED sequence=\d+ dropped=\d+\r\n",
                                                timeout=2.0, binary_prefix=True)
        return self.decode(prefix)

    def device_info(self):
        return {"type": "eeg26", "channel_count": CHANNEL_COUNT, "protocol_version": PROTOCOL_VERSION,
                "sample_rate_hz": self.sample_rate_hz, "config1_readback": self.config1,
                "configuration_source": "pc",
                "channel_units": "signed ADC raw count", "timestamp_clock_hz": 1000000}

    def statistics(self):
        dropped = re.search(r"dropped=(\d+)", self.stop_reply or "")
        return self.parser.statistics() | {"serial_timeouts": self.serial_timeouts,
                "timestamp_gap_events": self.timestamp_gap_events,
                "max_sample_interval_us": self.max_sample_interval_us,
                "device_duration_s": ((self.last_device_time - self.first_device_time) / 1e6
                                      if self.first_device_time is not None else None),
                "stop_acknowledged": self.stop_reply is not None,
                "firmware_dropped": int(dropped[1]) if dropped else None,
                "commands": self.command_log}


BACKENDS = {"legacy": LegacyBackend, "eeg26": EEG26Backend}


def resolve_device(device):
    if device:
        return device
    print("Select EEG device:\n1: Legacy EEG\n2: EEG26 (STM32H743 + dual ADS1299)")
    choice = input("Select: ").strip()
    if choice not in ("1", "2"):
        raise ValueError("Device selection must be 1 or 2")
    return {"1": "legacy", "2": "eeg26"}[choice]
