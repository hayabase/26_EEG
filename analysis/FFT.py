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
class ChannelFft:
    name: str
    time_s: np.ndarray
    value: np.ndarray
    sample_rate_hz: float
    frequency_hz: np.ndarray
    amplitude: np.ndarray
    peak_frequency_hz: float
    peak_amplitude: float
    target_frequency_hz: Optional[float]
    target_amplitude: Optional[float]


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


def parse_time_range(text: Optional[str]) -> Optional[Tuple[float, float]]:
    if text is None:
        return None
    parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--time-range must be START,END in seconds")
    try:
        start_s = float(parts[0])
        end_s = float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--time-range values must be numbers") from exc
    if start_s < 0.0:
        raise argparse.ArgumentTypeError("--time-range START must be 0 or greater")
    if end_s <= start_s:
        raise argparse.ArgumentTypeError("--time-range END must be greater than START")
    return start_s, end_s


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


def crop_series_by_time_range(
    series: Dict[str, Tuple[np.ndarray, np.ndarray]],
    time_range: Optional[Tuple[float, float]],
    time_origin: str,
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    if time_range is None:
        return series
    if not series:
        return series

    start_s, end_s = time_range
    if time_origin == "data":
        origin_s = min(float(time_s[0]) for time_s, _value in series.values() if time_s.size)
    else:
        origin_s = 0.0

    crop_start_s = origin_s + start_s
    crop_end_s = origin_s + end_s
    cropped: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for channel, (time_s, value) in series.items():
        mask = (time_s >= crop_start_s) & (time_s <= crop_end_s)
        if np.count_nonzero(mask) >= 2:
            cropped[channel] = (time_s[mask], value[mask])
    return cropped


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
        raise ValueError("not enough time samples for FFT")

    sample_interval_s = float(np.median(positive_dt))
    sample_rate_hz = 1.0 / sample_interval_s
    sample_count = int(np.floor((time_s[-1] - time_s[0]) * sample_rate_hz)) + 1
    if sample_count < 2:
        raise ValueError("not enough duration for FFT")

    uniform_time_s = time_s[0] + np.arange(sample_count, dtype=float) / sample_rate_hz
    uniform_value = np.interp(uniform_time_s, time_s, value)
    return uniform_time_s, uniform_value, sample_rate_hz


def compute_fft(
    channel: str,
    time_s: np.ndarray,
    value: np.ndarray,
    min_freq_hz: float,
    max_freq_hz: float,
    target_frequency_hz: Optional[float],
) -> ChannelFft:
    uniform_time_s, uniform_value, sample_rate_hz = build_uniform_series(time_s, value)
    centered_value = uniform_value - float(np.mean(uniform_value))

    if centered_value.size < 3:
        window = np.ones(centered_value.size)
    else:
        window = np.hanning(centered_value.size)
    windowed_value = centered_value * window
    frequency_hz = np.fft.rfftfreq(windowed_value.size, d=1.0 / sample_rate_hz)
    amplitude = np.abs(np.fft.rfft(windowed_value)) * 2.0 / float(np.sum(window))

    peak_mask = (frequency_hz >= min_freq_hz) & (frequency_hz <= max_freq_hz)
    peak_mask &= frequency_hz > 0.0
    if not np.any(peak_mask):
        peak_frequency_hz = float("nan")
        peak_amplitude = float("nan")
    else:
        peak_indices = np.flatnonzero(peak_mask)
        peak_index = int(peak_indices[np.argmax(amplitude[peak_indices])])
        peak_frequency_hz = float(frequency_hz[peak_index])
        peak_amplitude = float(amplitude[peak_index])

    if target_frequency_hz is None:
        target_amplitude = None
    else:
        target_index = int(np.argmin(np.abs(frequency_hz - target_frequency_hz)))
        target_amplitude = float(amplitude[target_index])

    return ChannelFft(
        name=channel,
        time_s=uniform_time_s,
        value=uniform_value,
        sample_rate_hz=sample_rate_hz,
        frequency_hz=frequency_hz,
        amplitude=amplitude,
        peak_frequency_hz=peak_frequency_hz,
        peak_amplitude=peak_amplitude,
        target_frequency_hz=target_frequency_hz,
        target_amplitude=target_amplitude,
    )


def max_abs_from_arrays(arrays: Iterable[np.ndarray]) -> Optional[float]:
    max_value = 0.0
    found = False
    for values in arrays:
        finite_values = np.asarray(values, dtype=float)
        finite_values = finite_values[np.isfinite(finite_values)]
        if finite_values.size == 0:
            continue
        max_value = max(max_value, float(np.max(np.abs(finite_values))))
        found = True
    if not found or max_value <= 0.0:
        return None
    return max_value


def set_symmetric_ylim_from_max(axis, max_abs_value: Optional[float]) -> None:
    if max_abs_value is None:
        return
    limit = max_abs_value * 1.05
    axis.set_ylim(-limit, limit)


def fft_amplitude_reference(
    fft_results: Sequence[ChannelFft],
    min_freq_hz: float,
    max_freq_hz: float,
) -> float:
    max_amplitude = 0.0
    for result in fft_results:
        freq_mask = (result.frequency_hz >= min_freq_hz) & (result.frequency_hz <= max_freq_hz)
        freq_mask &= result.frequency_hz > 0.0
        if not np.any(freq_mask):
            continue
        finite_amplitude = result.amplitude[freq_mask]
        finite_amplitude = finite_amplitude[np.isfinite(finite_amplitude)]
        if finite_amplitude.size:
            max_amplitude = max(max_amplitude, float(np.max(finite_amplitude)))
    return max_amplitude if max_amplitude > 0.0 else 1.0


def enable_channel_check_panel(fig, channel_artists: dict[int, list], labels: list[str]) -> None:
    try:
        from matplotlib.widgets import CheckButtons
    except Exception:
        return

    if not channel_artists:
        return

    fig.subplots_adjust(left=0.20, right=0.96, bottom=0.08, top=0.93, wspace=0.28)
    panel_height = min(0.42, 0.045 * len(labels) + 0.08)
    ax_box = fig.add_axes([0.015, 0.94 - panel_height, 0.16, panel_height])
    buttons = CheckButtons(ax_box, labels, [True] * len(labels))
    ax_box.set_title("Show", fontsize=9)

    def on_clicked(label: str) -> None:
        try:
            index = labels.index(label)
        except ValueError:
            return
        artists = channel_artists.get(index, [])
        if not artists:
            return
        visible = not artists[0].get_visible()
        for artist in artists:
            artist.set_visible(visible)
        fig.canvas.draw_idle()

    buttons.on_clicked(on_clicked)
    fig._channel_check_buttons = buttons


def plot_fft(
    fft_results: Sequence[ChannelFft],
    run_dir: Path,
    phase: str,
    time_range: Optional[Tuple[float, float]],
    time_origin: str,
    min_freq_hz: float,
    max_freq_hz: float,
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

    fig, (time_ax, fft_ax) = plt.subplots(1, 2, figsize=(13, 5))
    time_label = ""
    if time_range is not None:
        time_label = f" / time={time_range[0]:.3f}-{time_range[1]:.3f} s ({time_origin})"
    fig.suptitle(f"FFT: {run_dir.name} / phase={phase}{time_label}")

    start_time_s = min(float(result.time_s[0]) for result in fft_results)
    time_max_abs = max_abs_from_arrays(result.value for result in fft_results)
    amplitude_reference = fft_amplitude_reference(fft_results, min_freq_hz, max_freq_hz)
    channel_artists: dict[int, list] = {}
    labels: List[str] = []
    for result_index, result in enumerate(fft_results):
        color = CHANNEL_COLORS.get(result.name)
        time_axis = result.time_s - start_time_s
        time_label = f"{result.name}, fs={result.sample_rate_hz:.1f} Hz"
        artists = []
        line_time, = time_ax.plot(
            time_axis,
            result.value,
            linewidth=0.8,
            color=color,
            label=time_label,
        )
        artists.append(line_time)

        freq_mask = (result.frequency_hz >= min_freq_hz) & (result.frequency_hz <= max_freq_hz)
        relative_amplitude = result.amplitude / amplitude_reference
        peak_relative_amplitude = result.peak_amplitude / amplitude_reference
        fft_label = (
            f"{result.name}, peak={result.peak_frequency_hz:.3f} Hz, "
            f"rel={peak_relative_amplitude:.3f}"
        )
        line_fft, = fft_ax.plot(
            result.frequency_hz[freq_mask],
            relative_amplitude[freq_mask],
            linewidth=1.0,
            color=color,
            label=fft_label,
        )
        artists.append(line_fft)
        if np.isfinite(result.peak_frequency_hz):
            peak_artist = fft_ax.scatter(
                [result.peak_frequency_hz],
                [peak_relative_amplitude],
                color=color,
                marker="o",
                s=24,
                zorder=3,
            )
            artists.append(peak_artist)
        channel_artists[result_index] = artists
        labels.append(result.name)

    time_ax.set_title("Waveform by channel")
    time_ax.set_xlabel("Time [s]")
    time_ax.set_ylabel("Value")
    set_symmetric_ylim_from_max(time_ax, time_max_abs)
    time_ax.grid(True, alpha=0.3)
    time_ax.legend(loc="best")

    fft_ax.set_title("FFT by channel")
    fft_ax.set_xlabel("Frequency [Hz]")
    fft_ax.set_ylabel("Relative amplitude [max across channels = 1]")
    fft_ax.set_ylim(bottom=0.0)
    fft_ax.grid(True, alpha=0.3)
    fft_ax.legend(loc="best")

    enable_channel_check_panel(fig, channel_artists, labels)

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Saved graph: {save_path}")

    if show:
        plt.show()


def default_save_path(run_dir: Path, phase: str) -> Path:
    return run_dir / f"fft_{run_dir.name}_{phase}.png"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FFT viewer for offline MAX2 measurement serial_samples.csv."
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
    parser.add_argument("--min-freq", type=float, default=0.5, help="Minimum FFT frequency.")
    parser.add_argument("--max-freq", type=float, default=60.0, help="Maximum FFT frequency.")
    parser.add_argument(
        "--time-range",
        type=parse_time_range,
        default=None,
        help=(
            "FFT time range in seconds as START,END. "
            "Default origin is selected data start, so --phase stimulus --time-range 0,3 "
            "uses the first 3 seconds of the stimulus data."
        ),
    )
    parser.add_argument(
        "--time-origin",
        choices=("data", "experiment"),
        default="data",
        help="Origin for --time-range. data=start of selected data, experiment=experiment_time_s 0.",
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
    if args.min_freq < 0.0:
        raise ValueError("--min-freq must be 0 or greater")
    if args.max_freq <= args.min_freq:
        raise ValueError("--max-freq must be greater than --min-freq")

    run_dir = resolve_run_dir(args.data_path)
    serial_csv = find_serial_csv(run_dir)
    target_frequency_hz = None
    if not args.no_target_marker:
        target_frequency_hz = args.target_freq
        if target_frequency_hz is None:
            target_frequency_hz = read_target_frequency_hz(run_dir)

    series = load_channel_series(serial_csv, args.phase, args.channels)
    series = crop_series_by_time_range(series, args.time_range, args.time_origin)
    if not series:
        raise RuntimeError(
            f"No valid channel data found for phase={args.phase}, "
            f"time_range={args.time_range}"
        )

    fft_results = [
        compute_fft(channel, time_s, value, args.min_freq, args.max_freq, target_frequency_hz)
        for channel, (time_s, value) in series.items()
    ]
    amplitude_reference = fft_amplitude_reference(fft_results, args.min_freq, args.max_freq)

    print(f"Run folder: {run_dir}")
    print(f"Serial CSV: {serial_csv}")
    print(f"Phase: {args.phase}")
    if args.time_range is not None:
        print(
            f"FFT time range: {args.time_range[0]:.3f}-{args.time_range[1]:.3f} s "
            f"(origin={args.time_origin})"
        )
    if target_frequency_hz is not None:
        print(f"Target frequency marker: {target_frequency_hz:.3f} Hz")
    print(f"Amplitude reference: {amplitude_reference:.6g} (max across selected channels)")
    for result in fft_results:
        relative_peak = result.peak_amplitude / amplitude_reference
        line = (
            f"{result.name}: samples={result.value.size}, "
            f"fs={result.sample_rate_hz:.2f} Hz, "
            f"peak={result.peak_frequency_hz:.3f} Hz, "
            f"amplitude={result.peak_amplitude:.3f}, "
            f"relative_amplitude={relative_peak:.3f}"
        )
        if result.target_amplitude is not None:
            relative_target = result.target_amplitude / amplitude_reference
            line += (
                f", target_amplitude={result.target_amplitude:.3f}, "
                f"target_relative_amplitude={relative_target:.3f}"
            )
        print(line)

    if args.save is None:
        save_path = None
    elif args.save == "":
        save_path = default_save_path(run_dir, args.phase)
    else:
        save_path = Path(args.save).expanduser()

    if save_path is not None or not args.no_show:
        plot_fft(
            fft_results=fft_results,
            run_dir=run_dir,
            phase=args.phase,
            time_range=args.time_range,
            time_origin=args.time_origin,
            min_freq_hz=args.min_freq,
            max_freq_hz=args.max_freq,
            save_path=save_path,
            show=not args.no_show,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
