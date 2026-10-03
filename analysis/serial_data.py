"""Shared channel discovery and EEG26 timing, with unchanged legacy row times."""
import argparse
import csv
import json
import re


def parse_channels(text):
    if text is None:
        return None
    channels = tuple(dict.fromkeys(p.strip() for p in text.split(",") if p.strip()))
    if not channels or any(not re.fullmatch(r"ch[1-9][0-9]*", c) for c in channels):
        raise argparse.ArgumentTypeError("Use comma-separated channel names, e.g. ch1,ch2,ch8,ch16")
    return channels


def resolve_channels(path, requested=None):
    with open(path, encoding="utf-8", newline="") as file:
        header = next(csv.reader(file))
    available = tuple(sorted((c for c in header if re.fullmatch(r"ch[1-9][0-9]*", c)),
                             key=lambda c: int(c[2:])))
    if requested is not None:
        missing = set(requested) - set(available)
        if missing:
            raise ValueError(f"Channels not present in CSV: {', '.join(sorted(missing))}")
        return requested
    return available


def read_sample_rate(path):
    metadata_path = path.parent / "metadata.json"
    if not metadata_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    device = metadata.get("device", {})
    if device.get("type") != "eeg26":
        return None
    rate = device.get("sample_rate_hz")
    if rate is None or rate <= 0:
        raise ValueError("EEG26 metadata has no verified sample_rate_hz")
    return float(rate)


def iter_rows(path):
    """Use DRDY intervals anchored to the first PC receipt; preserve host phase clocks.

    USB batch receipts cannot estimate the sampling rate. This anchor retains an
    unknown USB latency offset; it does not imply hardware stimulus synchronization.
    """
    rate = read_sample_rate(path)
    anchor_pc = anchor_device = None
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if row.get("parse_error") or row.get("crc_ok") == "0":
                continue
            if rate is not None:
                device_time = int(row["device_time_unwrapped_us"])
                if anchor_device is None:
                    anchor_device = device_time
                    anchor_pc = float(row["experiment_time_s"])
                row["experiment_time_s"] = str(anchor_pc + (device_time - anchor_device) / 1e6)
            yield row
