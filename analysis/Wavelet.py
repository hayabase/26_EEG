# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOTS = (
    REPO_ROOT / "measurement" / "measurement_data",
    REPO_ROOT / "measurement_data",
)
SERIAL_CSV_NAME = "serial_samples.csv"
CHANNEL_NAMES = ("ch1", "ch2", "ch3")
PHASE_CHOICES = ("all", "idle", "fixation_before", "stimulus", "fixation_after", "finished")
CHANNEL_COLORS = {
    "ch1": "tab:blue",
    "ch2": "tab:orange",
    "ch3": "tab:green",
}


@dataclass(frozen=True)
class ChannelWavelet:
    name: str
    time_s: np.ndarray
    value: np.ndarray
    sample_rate_hz: float
    frequency_hz: np.ndarray
    power: np.ndarray
    peak_time_s: float
    peak_frequency_hz: float
    peak_power: float
    target_frequency_hz: Optional[float]
    target_mean_power: Optional[float]


def resolve_run_dir(data_path: str) -> Path:
    path = Path(data_path).expanduser()
    if path.exists():
        if path.is_file() and path.name == SERIAL_CSV_NAME:
            return path.parent
        return path

    for base_dir in DEFAULT_DATA_ROOTS:
        candidate = base_dir / data_path
        if candidate.exists():
            return candidate

    searched = "\n".join(f"  {base_dir / data_path}" for base_dir in DEFAULT_DATA_ROOTS)
    raise FileNotFoundError(
        f"Data folder not found: {data_path}\n"
        f"Pass a full path or a run folder name.\n"
        f"Searched:\n{searched}"
    )


def find_serial_csv(run_dir: Path) -> Path:
    serial_csv = run_dir / SERIAL_CSV_NAME
    if not serial_csv.exists():
        raise FileNotFoundError(f"{SERIAL_CSV_NAME} not found in {run_dir}")
    return serial_csv


def read_target_frequency_hz(run_dir: Path) -> Optional[float]:
    metadata_json = run_dir / "metadata.json"
    if not metadata_json.exists():
        return None

    try:
        with open(metadata_json, encoding="utf-8") as file:
            metadata = json.load(file)
    except Exception:
        return None

    stimuli = metadata.get("config", {}).get("stimuli", [])
    if not stimuli:
        return None

    first_stimulus = stimuli[0]
    if not isinstance(first_stimulus, dict):
        return None

    try:
        return float(first_stimulus["frequency_hz"])
    except (KeyError, TypeError, ValueError):
        return None


def parse_channels(text: Optional[str]) -> Tuple[str, ...]:
    if text is None:
        return CHANNEL_NAMES

    channels = tuple(part.strip() for part in text.split(",") if part.strip())
    invalid = [channel for channel in channels if channel not in CHANNEL_NAMES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unknown channel: {', '.join(invalid)}. Use ch1,ch2,ch3."
        )
    if not channels:
        raise argparse.ArgumentTypeError("at least one channel is required")
    return channels


def iter_valid_rows(serial_csv: Path, phase: str) -> Iterable[Dict[str, str]]:
    with open(serial_csv, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("parse_error"):
                continue
            if phase != "all" and row.get("phase_name") != phase:
                continue
            yield row


def load_channel_series(
    serial_csv: Path,
    phase: str,
    channels: Sequence[str],
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    times_by_channel: Dict[str, List[float]] = {channel: [] for channel in channels}
    values_by_channel: Dict[str, List[float]] = {channel: [] for channel in channels}

    for row in iter_valid_rows(serial_csv, phase):
        try:
            time_s = float(row["experiment_time_s"])
        except (KeyError, ValueError):
            continue

        for channel in channels:
            value_text = row.get(channel, "")
            if value_text == "":
                continue
            try:
                value = float(value_text)
            except ValueError:
                continue
            times_by_channel[channel].append(time_s)
            values_by_channel[channel].append(value)

    series: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for channel in channels:
        if values_by_channel[channel]:
            series[channel] = (
                np.asarray(times_by_channel[channel], dtype=float),
                np.asarray(values_by_channel[channel], dtype=float),
            )
    return series


def build_uniform_series(time_s: np.ndarray, value: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    order = np.argsort(time_s)
    time_s = time_s[order]
    value = value[order]

    unique_time, unique_indices = np.unique(time_s, return_index=True)
    time_s = unique_time
    value = value[unique_indices]

    positive_dt = np.diff(time_s)
    positive_dt = positive_dt[positive_dt > 0.0]
    if positive_dt.size == 0:
        raise ValueError("not enough time samples for wavelet transform")

    sample_interval_s = float(np.median(positive_dt))
    sample_rate_hz = 1.0 / sample_interval_s
    sample_count = int(np.floor((time_s[-1] - time_s[0]) * sample_rate_hz)) + 1
    if sample_count < 2:
        raise ValueError("not enough duration for wavelet transform")

    uniform_time_s = time_s[0] + np.arange(sample_count, dtype=float) / sample_rate_hz
    uniform_value = np.interp(uniform_time_s, time_s, value)
    return uniform_time_s, uniform_value, sample_rate_hz


def build_frequencies(min_freq_hz: float, max_freq_hz: float, count: int, scale: str) -> np.ndarray:
    if scale == "log":
        return np.geomspace(min_freq_hz, max_freq_hz, count)
    return np.linspace(min_freq_hz, max_freq_hz, count)


def next_power_of_two(value: int) -> int:
    return 1 << (value - 1).bit_length()


def fft_convolve_same(signal: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    full_len = signal.size + kernel.size - 1
    fft_len = next_power_of_two(full_len)
    signal_fft = np.fft.fft(signal, fft_len)
    kernel_fft = np.fft.fft(kernel, fft_len)
    convolved = np.fft.ifft(signal_fft * kernel_fft)[:full_len]
    start = (kernel.size - 1) // 2
    return convolved[start : start + signal.size]


def morlet_wavelet(
    frequency_hz: float,
    sample_rate_hz: float,
    cycles: float,
    support_sigma: float,
) -> np.ndarray:
    sigma_t = cycles / (2.0 * np.pi * frequency_hz)
    half_width = max(1, int(np.ceil(support_sigma * sigma_t * sample_rate_hz)))
    time_s = np.arange(-half_width, half_width + 1, dtype=float) / sample_rate_hz
    wavelet = np.exp(2j * np.pi * frequency_hz * time_s)
    wavelet *= np.exp(-(time_s**2) / (2.0 * sigma_t**2))
    wavelet -= np.mean(wavelet)

    norm = np.sqrt(np.sum(np.abs(wavelet) ** 2.0))
    if norm > 0.0:
        wavelet /= norm
    return wavelet


def compute_wavelet(
    channel: str,
    time_s: np.ndarray,
    value: np.ndarray,
    frequencies_hz: np.ndarray,
    cycles: float,
    support_sigma: float,
    edge_ignore_sec: float,
    target_frequency_hz: Optional[float],
) -> ChannelWavelet:
    uniform_time_s, uniform_value, sample_rate_hz = build_uniform_series(time_s, value)
    centered_value = uniform_value - float(np.mean(uniform_value))
    power = np.empty((frequencies_hz.size, centered_value.size), dtype=float)

    for freq_index, frequency_hz in enumerate(frequencies_hz):
        wavelet = morlet_wavelet(frequency_hz, sample_rate_hz, cycles, support_sigma)
        kernel = np.conj(wavelet[::-1])
        coefficients = fft_convolve_same(centered_value, kernel)
        power[freq_index] = np.abs(coefficients) ** 2.0

    search_power = power.copy()
    edge_ignore_samples = int(round(edge_ignore_sec * sample_rate_hz))
    if edge_ignore_samples > 0 and edge_ignore_samples * 2 < search_power.shape[1]:
        search_power[:, :edge_ignore_samples] = -np.inf
        search_power[:, -edge_ignore_samples:] = -np.inf
    if not np.any(np.isfinite(search_power)):
        search_power = power

    peak_flat_index = int(np.argmax(search_power))
    peak_freq_index, peak_time_index = np.unravel_index(peak_flat_index, search_power.shape)
    peak_frequency_hz = float(frequencies_hz[peak_freq_index])
    peak_time_s = float(uniform_time_s[peak_time_index])
    peak_power = float(power[peak_freq_index, peak_time_index])

    if target_frequency_hz is None:
        target_mean_power = None
    else:
        target_index = int(np.argmin(np.abs(frequencies_hz - target_frequency_hz)))
        target_power = power[target_index]
        if edge_ignore_samples > 0 and edge_ignore_samples * 2 < target_power.size:
            target_power = target_power[edge_ignore_samples:-edge_ignore_samples]
        target_mean_power = float(np.mean(target_power))

    return ChannelWavelet(
        name=channel,
        time_s=uniform_time_s,
        value=uniform_value,
        sample_rate_hz=sample_rate_hz,
        frequency_hz=frequencies_hz,
        power=power,
        peak_time_s=peak_time_s,
        peak_frequency_hz=peak_frequency_hz,
        peak_power=peak_power,
        target_frequency_hz=target_frequency_hz,
        target_mean_power=target_mean_power,
    )


def scale_power_for_plot(power: np.ndarray, power_scale: str) -> Tuple[np.ndarray, str]:
    if power_scale == "linear":
        return power, "Power"

    reference = float(np.max(power))
    if reference <= 0.0:
        reference = 1.0
    power_db = 10.0 * np.log10((power / reference) + 1e-12)
    return power_db, "Power [dB re channel max]"


def make_edges(values: np.ndarray) -> np.ndarray:
    if values.size == 1:
        width = 0.5
        return np.asarray([values[0] - width, values[0] + width], dtype=float)

    edges = np.empty(values.size + 1, dtype=float)
    edges[1:-1] = (values[:-1] + values[1:]) / 2.0
    edges[0] = values[0] - (edges[1] - values[0])
    edges[-1] = values[-1] + (values[-1] - edges[-2])
    return edges


def plot_wavelet(
    wavelet_results: Sequence[ChannelWavelet],
    run_dir: Path,
    phase: str,
    power_scale: str,
    save_path: Optional[Path],
    show: bool,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "matplotlib is required for graph display. "
            "Install it with: conda install matplotlib"
        ) from exc

    row_count = 1 + len(wavelet_results)
    fig, axes = plt.subplots(
        row_count,
        1,
        figsize=(13, max(5, 2.6 * row_count)),
        sharex=True,
        constrained_layout=True,
    )
    if row_count == 1:
        axes = [axes]

    fig.suptitle(f"Wavelet transform: {run_dir.name} / phase={phase}")
    waveform_ax = axes[0]
    start_time_s = min(float(result.time_s[0]) for result in wavelet_results)

    for result in wavelet_results:
        color = CHANNEL_COLORS.get(result.name)
        waveform_ax.plot(
            result.time_s - start_time_s,
            result.value,
            linewidth=0.8,
            color=color,
            label=f"{result.name}, fs={result.sample_rate_hz:.1f} Hz",
        )
    waveform_ax.set_title("Waveform by channel")
    waveform_ax.set_ylabel("Value")
    waveform_ax.grid(True, alpha=0.3)
    waveform_ax.legend(loc="best")

    target_frequency_hz = next(
        (
            result.target_frequency_hz
            for result in wavelet_results
            if result.target_frequency_hz is not None
        ),
        None,
    )

    for axis, result in zip(axes[1:], wavelet_results):
        plot_power, colorbar_label = scale_power_for_plot(result.power, power_scale)
        time_axis = result.time_s - start_time_s
        image = axis.pcolormesh(
            make_edges(time_axis),
            make_edges(result.frequency_hz),
            plot_power,
            cmap="viridis",
            shading="auto",
        )
        if target_frequency_hz is not None:
            axis.axhline(
                target_frequency_hz,
                color="tab:red",
                linestyle=":",
                linewidth=1.2,
                label=f"target={target_frequency_hz:.3f} Hz",
            )
        axis.scatter(
            [result.peak_time_s - start_time_s],
            [result.peak_frequency_hz],
            color="white",
            edgecolor="black",
            s=28,
            zorder=3,
            label=f"peak={result.peak_frequency_hz:.3f} Hz",
        )
        axis.set_title(f"{result.name} wavelet power")
        axis.set_ylabel("Frequency [Hz]")
        axis.legend(loc="upper right")
        fig.colorbar(image, ax=axis, label=colorbar_label)

    axes[-1].set_xlabel("Time [s]")

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Saved graph: {save_path}")

    if show:
        plt.show()


def default_save_path(run_dir: Path, phase: str) -> Path:
    return run_dir / f"wavelet_{run_dir.name}_{phase}.png"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wavelet transform viewer for offline MAX2 serial_samples.csv."
    )
    parser.add_argument(
        "data_path",
        help=(
            "Run folder path or run folder name, e.g. "
            "measurement/measurement_data/max2_parallel_20260520_185539 "
            "or max2_parallel_20260520_185539."
        ),
    )
    parser.add_argument(
        "--phase",
        choices=PHASE_CHOICES,
        default="all",
        help="Phase to analyze. Default is all. Use stimulus for the flicker-only section.",
    )
    parser.add_argument(
        "--channels",
        type=parse_channels,
        default=CHANNEL_NAMES,
        help="Comma-separated channels, e.g. ch1,ch2 or ch1,ch2,ch3.",
    )
    parser.add_argument("--min-freq", type=float, default=2.0, help="Minimum wavelet frequency.")
    parser.add_argument("--max-freq", type=float, default=45.0, help="Maximum wavelet frequency.")
    parser.add_argument("--freq-count", type=int, default=64, help="Number of wavelet frequencies.")
    parser.add_argument(
        "--freq-scale",
        choices=("linear", "log"),
        default="linear",
        help="Frequency spacing for the wavelet analysis.",
    )
    parser.add_argument(
        "--wavelet-cycles",
        type=float,
        default=6.0,
        help="Morlet wavelet cycle count. Higher values improve frequency resolution.",
    )
    parser.add_argument(
        "--support-sigma",
        type=float,
        default=4.0,
        help="Wavelet support width in gaussian sigma units.",
    )
    parser.add_argument(
        "--edge-ignore-sec",
        type=float,
        default=0.25,
        help="Ignore this many seconds at both edges for peak detection.",
    )
    parser.add_argument(
        "--power-scale",
        choices=("db", "linear"),
        default="db",
        help="Scalogram color scale.",
    )
    parser.add_argument(
        "--target-freq",
        type=float,
        default=None,
        help="Target marker frequency. If omitted, use metadata.json when available.",
    )
    parser.add_argument(
        "--no-target-marker",
        action="store_true",
        help="Do not draw the target frequency marker.",
    )
    parser.add_argument(
        "--save",
        nargs="?",
        const="",
        default=None,
        help="Save graph PNG. If no path is given, save it in the run folder.",
    )
    parser.add_argument("--no-show", action="store_true", help="Do not display the graph window.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.min_freq <= 0.0:
        raise ValueError("--min-freq must be greater than 0")
    if args.max_freq <= args.min_freq:
        raise ValueError("--max-freq must be greater than --min-freq")
    if args.freq_count < 2:
        raise ValueError("--freq-count must be 2 or greater")
    if args.wavelet_cycles <= 0.0:
        raise ValueError("--wavelet-cycles must be greater than 0")
    if args.support_sigma <= 0.0:
        raise ValueError("--support-sigma must be greater than 0")
    if args.edge_ignore_sec < 0.0:
        raise ValueError("--edge-ignore-sec must be 0 or greater")

    run_dir = resolve_run_dir(args.data_path)
    serial_csv = find_serial_csv(run_dir)
    target_frequency_hz = None
    if not args.no_target_marker:
        target_frequency_hz = args.target_freq
        if target_frequency_hz is None:
            target_frequency_hz = read_target_frequency_hz(run_dir)

    series = load_channel_series(serial_csv, args.phase, args.channels)
    if not series:
        raise RuntimeError(f"No valid channel data found for phase={args.phase}")

    frequencies_hz = build_frequencies(
        min_freq_hz=args.min_freq,
        max_freq_hz=args.max_freq,
        count=args.freq_count,
        scale=args.freq_scale,
    )
    wavelet_results = [
        compute_wavelet(
            channel=channel,
            time_s=time_s,
            value=value,
            frequencies_hz=frequencies_hz,
            cycles=args.wavelet_cycles,
            support_sigma=args.support_sigma,
            edge_ignore_sec=args.edge_ignore_sec,
            target_frequency_hz=target_frequency_hz,
        )
        for channel, (time_s, value) in series.items()
    ]

    print(f"Run folder: {run_dir}")
    print(f"Serial CSV: {serial_csv}")
    print(f"Phase: {args.phase}")
    print(
        f"Wavelet: Morlet, cycles={args.wavelet_cycles:.2f}, "
        f"frequencies={args.min_freq:.3f}-{args.max_freq:.3f} Hz ({args.freq_count}), "
        f"edge_ignore={args.edge_ignore_sec:.3f} s"
    )
    if target_frequency_hz is not None:
        print(f"Target frequency marker: {target_frequency_hz:.3f} Hz")
    for result in wavelet_results:
        line = (
            f"{result.name}: samples={result.value.size}, "
            f"fs={result.sample_rate_hz:.2f} Hz, "
            f"peak={result.peak_frequency_hz:.3f} Hz at "
            f"t={result.peak_time_s - result.time_s[0]:.3f} s, "
            f"power={result.peak_power:.3f}"
        )
        if result.target_mean_power is not None:
            line += f", target_mean_power={result.target_mean_power:.3f}"
        print(line)

    if args.save is None:
        save_path = None
    elif args.save == "":
        save_path = default_save_path(run_dir, args.phase)
    else:
        save_path = Path(args.save).expanduser()

    if save_path is not None or not args.no_show:
        plot_wavelet(
            wavelet_results=wavelet_results,
            run_dir=run_dir,
            phase=args.phase,
            power_scale=args.power_scale,
            save_path=save_path,
            show=not args.no_show,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
