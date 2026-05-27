# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


# ===== 初期値設定 =====
# BPF係数は別プロジェクトで作成した値をここへ入れる.
DEFAULT_BANDPASS_A = (
    1,
    -7.94473516723874518,
    27.6300377648309912,
    -54.9401269394967926,
    68.3159002695237376,
    -54.3973535593714743,
    27.0868007506904895,
    -7.71158761895624867,
    0.96106450024650425,
)  # IIR分母係数a. 既定値は10Hz付近を強調する低Qフィルタ.9~11Hz,3db, cheby2, 遅延2.5ms
DEFAULT_BANDPASS_B = (
    0.00982032454691744716,
    -0.0783414904343748136,
    0.273643937630629608,
    -0.546627525990940666,
    0.683009508497821605,
    -0.546627525990940666,
    0.273643937630629608,
    -0.0783414904343748275,
    0.00982032454691744716,
)  # IIR分子係数b. DEFAULT_BANDPASS_Aと同じ設計の係数.

# IIRノッチ係数. design_iir_notch_filter.py で作成した値をここへ入れる.
# Rank 1: method=iirnotch, Q=100
DEFAULT_NOTCH_A = (
    1,
    -1.89912988389104265,
    0.996863331833437893,
)  # IIRノッチ分母係数a. 既定値は50Hz, Q=100.
DEFAULT_NOTCH_B = (
    0.998431665916718947,
    -1.89912988389104265,
    0.998431665916718947,
)  # IIRノッチ分子係数b. DEFAULT_NOTCH_Aと同じ設計の係数.

DEFAULT_CHANNELS = ("ch1", "ch2", "ch3")  # 解析する既定チャンネル.
DEFAULT_PHASES = ("fixation_before", "stimulus", "fixation_after")  # グラフ化する既定フェイズ.
DEFAULT_PEAK_MODE = "max"  # 各周期内のピーク検出方法. max, min, abs.
DEFAULT_FILTER_DELAY_MS = 0.0  # BPFの遅延補正量. 正の値でフィルタ出力を前に戻す.
DEFAULT_ALPHA = 0.18  # 周期ごとの重ね描き線の透明度.
DEFAULT_LINE_WIDTH = 0.8  # 周期ごとの重ね描き線幅.
DEFAULT_AVERAGE_LINE_WIDTH = 2.2  # 平均波形の線幅.


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOTS = (
    REPO_ROOT / "measurement" / "measurement_data",
    REPO_ROOT / "measurement_data",
)
SERIAL_CSV_NAME = "serial_samples.csv"
EVENTS_CSV_NAME = "events.csv"
FRAMES_CSV_NAME = "frames.csv"
PHASE_CHOICES = ("all", "idle", "fixation_before", "stimulus", "fixation_after", "finished")
CHANNEL_COLORS = {
    "ch1": "tab:blue",
    "ch2": "tab:orange",
    "ch3": "tab:green",
}


@dataclass(frozen=True)
class MetadataInfo:
    frequency_hz: Optional[float]
    start_on: Optional[bool]


@dataclass(frozen=True)
class PhaseInterval:
    name: str
    start_s: float
    end_s: float
    source: str
    cycle_start_s: Optional[np.ndarray] = None
    cycle_source: str = "ideal"

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class UniformChannel:
    name: str
    time_s: np.ndarray
    raw: np.ndarray
    filtered: np.ndarray
    sample_rate_hz: float


@dataclass(frozen=True)
class FoldedPhase:
    phase: PhaseInterval
    channel: str
    phase_axis_ms: np.ndarray
    segments: np.ndarray
    average_segment: np.ndarray
    cycle_start_s: np.ndarray
    cycle_duration_s: np.ndarray
    peak_time_ms: np.ndarray
    peak_value: np.ndarray


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


def find_required_file(run_dir: Path, name: str) -> Path:
    path = run_dir / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found in {run_dir}")
    return path


def read_metadata(run_dir: Path) -> MetadataInfo:
    metadata_json = run_dir / "metadata.json"
    if not metadata_json.exists():
        return MetadataInfo(frequency_hz=None, start_on=None)

    try:
        with open(metadata_json, encoding="utf-8") as file:
            metadata = json.load(file)
    except Exception:
        return MetadataInfo(frequency_hz=None, start_on=None)

    stimuli = metadata.get("config", {}).get("stimuli", [])
    if not stimuli or not isinstance(stimuli[0], dict):
        return MetadataInfo(frequency_hz=None, start_on=None)

    stimulus = stimuli[0]
    try:
        frequency_hz = float(stimulus["frequency_hz"])
    except (KeyError, TypeError, ValueError):
        frequency_hz = None

    start_on = stimulus.get("start_on")
    if not isinstance(start_on, bool):
        start_on = None

    return MetadataInfo(frequency_hz=frequency_hz, start_on=start_on)


def parse_channels(text: Optional[str]) -> Tuple[str, ...]:
    if text is None:
        return DEFAULT_CHANNELS

    channels = tuple(part.strip() for part in text.split(",") if part.strip())
    invalid = [channel for channel in channels if channel not in DEFAULT_CHANNELS]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unknown channel: {', '.join(invalid)}. Use ch1,ch2,ch3."
        )
    if not channels:
        raise argparse.ArgumentTypeError("at least one channel is required")
    return channels


def parse_phases(text: Optional[str]) -> Tuple[str, ...]:
    if text is None:
        return DEFAULT_PHASES

    phases = tuple(part.strip() for part in text.split(",") if part.strip())
    if "all" in phases:
        return DEFAULT_PHASES
    invalid = [phase for phase in phases if phase not in PHASE_CHOICES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"unknown phase: {', '.join(invalid)}. Use all or phase_name values."
        )
    if not phases:
        raise argparse.ArgumentTypeError("at least one phase is required")
    return phases


def parse_coefficients(text: Optional[str], default: Sequence[float]) -> np.ndarray:
    if text is None:
        return np.asarray(default, dtype=float)

    try:
        values = [float(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("filter coefficients must be comma-separated numbers") from exc
    if not values:
        raise argparse.ArgumentTypeError("filter coefficients cannot be empty")
    return np.asarray(values, dtype=float)


def parse_on_off(text: str) -> bool:
    normalized = text.strip().lower()
    if normalized in ("on", "true", "1", "yes", "y"):
        return True
    if normalized in ("off", "false", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError("use on/off")


def read_event_phase_intervals(events_csv: Path, phases: Sequence[str]) -> List[PhaseInterval]:
    rows: List[Dict[str, str]] = []
    with open(events_csv, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    intervals: List[PhaseInterval] = []
    wanted = set(phases)
    for phase in phases:
        start_time = None
        end_time = None
        for row in rows:
            if row.get("event_name") == f"{phase}_start":
                start_time = float(row["experiment_time_s"])
            elif row.get("event_name") == f"{phase}_end":
                end_time = float(row["experiment_time_s"])
        if start_time is None or end_time is None:
            continue
        if phase in wanted and end_time > start_time:
            intervals.append(
                PhaseInterval(name=phase, start_s=start_time, end_s=end_time, source="events")
            )
    return intervals


def parse_frame_state(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def find_stimulus_on_column(fieldnames: Optional[Sequence[str]]) -> Optional[str]:
    if not fieldnames:
        return None
    candidates = [
        name
        for name in fieldnames
        if name.endswith("_on") and name not in {"stimulus_active"}
    ]
    return candidates[0] if candidates else None


def read_frame_rows(frames_csv: Path) -> Tuple[List[Dict[str, str]], Optional[str]]:
    with open(frames_csv, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        return rows, find_stimulus_on_column(reader.fieldnames)


def extract_actual_cycle_starts(
    phase_rows: Sequence[Dict[str, str]],
    stimulus_on_column: Optional[str],
    start_on: Optional[bool],
    period_s: float,
    phase_end_s: float,
) -> Optional[np.ndarray]:
    if stimulus_on_column is None or start_on is None or not phase_rows:
        return None

    target_state = 1 if start_on else 0
    cycle_starts: List[float] = []
    previous_state: Optional[int] = None

    for row in phase_rows:
        state = parse_frame_state(row.get(stimulus_on_column, ""))
        if state is None:
            continue
        try:
            time_s = float(row["experiment_time_s"])
        except (KeyError, ValueError):
            continue

        if state == target_state and previous_state != target_state:
            if time_s + period_s <= phase_end_s + 1e-9:
                cycle_starts.append(time_s)
        previous_state = state

    if len(cycle_starts) < 2:
        return None
    return np.asarray(cycle_starts, dtype=float)


def read_frame_phase_intervals(
    frames_csv: Path,
    phases: Sequence[str],
    frequency_hz: float,
    start_on: Optional[bool],
) -> List[PhaseInterval]:
    raw_rows, stimulus_on_column = read_frame_rows(frames_csv)
    rows: List[Tuple[str, float, Dict[str, str]]] = []
    for row in raw_rows:
        phase_name = row.get("phase_name", "")
        try:
            experiment_time_s = float(row["experiment_time_s"])
        except (KeyError, ValueError):
            continue
        rows.append((phase_name, experiment_time_s, row))

    if not rows:
        return []

    period_s = 1.0 / frequency_hz
    frame_times = np.asarray([time_s for _, time_s, _ in rows], dtype=float)
    frame_dt = np.diff(frame_times)
    frame_dt = frame_dt[frame_dt > 0.0]
    estimated_frame_interval_s = float(np.median(frame_dt)) if frame_dt.size else 0.0

    intervals: List[PhaseInterval] = []
    for phase in phases:
        indices = [
            index
            for index, (phase_name, _, _) in enumerate(rows)
            if phase_name == phase
        ]
        if not indices:
            continue
        start_index = indices[0]
        end_index = indices[-1]
        start_s = rows[start_index][1]
        if end_index + 1 < len(rows):
            end_s = rows[end_index + 1][1]
        else:
            end_s = rows[end_index][1] + estimated_frame_interval_s
        if end_s > start_s:
            phase_rows = [rows[index][2] for index in indices]
            cycle_start_s = None
            cycle_source = "ideal"
            if phase == "stimulus":
                cycle_start_s = extract_actual_cycle_starts(
                    phase_rows=phase_rows,
                    stimulus_on_column=stimulus_on_column,
                    start_on=start_on,
                    period_s=period_s,
                    phase_end_s=end_s,
                )
                if cycle_start_s is not None:
                    cycle_source = f"frames:{stimulus_on_column}"
            intervals.append(
                PhaseInterval(
                    name=phase,
                    start_s=start_s,
                    end_s=end_s,
                    source="frames",
                    cycle_start_s=cycle_start_s,
                    cycle_source=cycle_source,
                )
            )
    return intervals


def iter_valid_rows(serial_csv: Path) -> Iterable[Dict[str, str]]:
    with open(serial_csv, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("parse_error"):
                continue
            yield row


def load_channel_series(
    serial_csv: Path,
    channels: Sequence[str],
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    times_by_channel: Dict[str, List[float]] = {channel: [] for channel in channels}
    values_by_channel: Dict[str, List[float]] = {channel: [] for channel in channels}

    for row in iter_valid_rows(serial_csv):
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
        raise ValueError("not enough time samples")

    sample_interval_s = float(np.median(positive_dt))
    sample_rate_hz = 1.0 / sample_interval_s
    sample_count = int(np.floor((time_s[-1] - time_s[0]) * sample_rate_hz)) + 1
    if sample_count < 2:
        raise ValueError("not enough duration")

    uniform_time_s = time_s[0] + np.arange(sample_count, dtype=float) / sample_rate_hz
    uniform_value = np.interp(uniform_time_s, time_s, value)
    return uniform_time_s, uniform_value, sample_rate_hz


def apply_iir_filter(signal: np.ndarray, b: np.ndarray, a: np.ndarray) -> np.ndarray:
    if a.size == 0 or b.size == 0:
        raise ValueError("filter coefficients cannot be empty")
    if a[0] == 0.0:
        raise ValueError("filter coefficient a[0] cannot be 0")

    a0 = float(a[0])
    a = a / a0
    b = b / a0
    output = np.zeros_like(signal, dtype=float)

    for index in range(signal.size):
        acc = 0.0
        for b_index, b_value in enumerate(b):
            source_index = index - b_index
            if source_index >= 0:
                acc += b_value * signal[source_index]
        for a_index in range(1, a.size):
            source_index = index - a_index
            if source_index >= 0:
                acc -= a[a_index] * output[source_index]
        output[index] = acc
    return output


def build_uniform_channels(
    series: Dict[str, Tuple[np.ndarray, np.ndarray]],
    b: np.ndarray,
    a: np.ndarray,
    use_filter: bool,
    filter_delay_ms: float,
    notch_b: Optional[np.ndarray] = None,
    notch_a: Optional[np.ndarray] = None,
    use_notch: bool = False,
) -> Dict[str, UniformChannel]:
    uniform_channels: Dict[str, UniformChannel] = {}
    for channel, (time_s, value) in series.items():
        uniform_time_s, uniform_value, sample_rate_hz = build_uniform_series(time_s, value)
        centered_value = uniform_value - float(np.mean(uniform_value))
        processed_value = centered_value
        if use_notch:
            if notch_a is None or notch_b is None:
                raise ValueError("notch coefficients are required when notch is on")
            processed_value = apply_iir_filter(processed_value, b=notch_b, a=notch_a)
        if use_filter:
            filtered = apply_iir_filter(processed_value, b=b, a=a)
        else:
            filtered = processed_value
        if filter_delay_ms != 0.0:
            delay_s = filter_delay_ms / 1000.0
            filtered = np.interp(uniform_time_s + delay_s, uniform_time_s, filtered)
        uniform_channels[channel] = UniformChannel(
            name=channel,
            time_s=uniform_time_s,
            raw=uniform_value,
            filtered=filtered,
            sample_rate_hz=sample_rate_hz,
        )
    return uniform_channels


def peak_index(segment: np.ndarray, mode: str) -> int:
    if mode == "min":
        return int(np.argmin(segment))
    if mode == "abs":
        return int(np.argmax(np.abs(segment)))
    return int(np.argmax(segment))


def fold_phase_channel(
    phase: PhaseInterval,
    channel: UniformChannel,
    frequency_hz: float,
    peak_mode: str,
) -> Optional[FoldedPhase]:
    period_s = 1.0 / frequency_hz
    sample_count = max(2, int(round(channel.sample_rate_hz * period_s)))
    phase_axis_s = np.arange(sample_count, dtype=float) / channel.sample_rate_hz
    phase_axis_ms = phase_axis_s * 1000.0

    if phase.cycle_start_s is not None and phase.cycle_start_s.size >= 2:
        cycle_starts = phase.cycle_start_s[:-1]
        cycle_durations = np.diff(phase.cycle_start_s)
    else:
        cycle_count = int(np.floor(phase.duration_s / period_s))
        if cycle_count <= 0:
            return None
        cycle_starts = phase.start_s + np.arange(cycle_count, dtype=float) * period_s
        cycle_durations = np.full(cycle_count, period_s, dtype=float)

    segments: List[np.ndarray] = []
    used_cycle_starts: List[float] = []
    used_cycle_durations: List[float] = []
    peak_times_ms: List[float] = []
    peak_values: List[float] = []

    for cycle_start_s, cycle_duration_s in zip(cycle_starts, cycle_durations):
        if cycle_start_s + cycle_duration_s > phase.end_s + 1e-9:
            continue
        normalized_phase = phase_axis_s / period_s
        sample_times_s = cycle_start_s + normalized_phase * cycle_duration_s
        if sample_times_s[-1] > channel.time_s[-1]:
            continue
        segment = np.interp(sample_times_s, channel.time_s, channel.filtered)
        if segment.size != sample_count:
            continue

        peak = peak_index(segment, peak_mode)
        segments.append(segment)
        used_cycle_starts.append(cycle_start_s)
        used_cycle_durations.append(cycle_duration_s)
        peak_times_ms.append(phase_axis_ms[peak])
        peak_values.append(float(segment[peak]))

    if not segments:
        return None

    segment_array = np.vstack(segments)
    return FoldedPhase(
        phase=phase,
        channel=channel.name,
        phase_axis_ms=phase_axis_ms,
        segments=segment_array,
        average_segment=np.mean(segment_array, axis=0),
        cycle_start_s=np.asarray(used_cycle_starts, dtype=float),
        cycle_duration_s=np.asarray(used_cycle_durations, dtype=float),
        peak_time_ms=np.asarray(peak_times_ms, dtype=float),
        peak_value=np.asarray(peak_values, dtype=float),
    )


def phase_zero_label(start_on: Optional[bool]) -> str:
    if start_on is True:
        return "phase 0 ms = ON start"
    if start_on is False:
        return "phase 0 ms = OFF start"
    return "phase 0 ms = phase start"


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


def plot_bandpass_overview(
    channels: Sequence[UniformChannel],
    phase_intervals: Sequence[PhaseInterval],
    run_dir: Path,
    save_dir: Optional[Path],
    show: bool,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "matplotlib is required for graph display. "
            "Install it with: conda install matplotlib"
        ) from exc

    fig, ax = plt.subplots(figsize=(13, 5))
    fig.suptitle(f"Filtered signal: {run_dir.name}")
    start_time_s = min(float(channel.time_s[0]) for channel in channels)
    channel_max_abs = max_abs_from_arrays(channel.filtered for channel in channels)
    for channel in channels:
        color = CHANNEL_COLORS.get(channel.name)
        ax.plot(
            channel.time_s - start_time_s,
            channel.filtered,
            linewidth=0.8,
            color=color,
            label=f"{channel.name}, fs={channel.sample_rate_hz:.1f} Hz",
        )
    for phase in phase_intervals:
        phase_start = phase.start_s - start_time_s
        phase_end = phase.end_s - start_time_s
        ax.axvline(phase_start, color="black", linestyle=":", linewidth=0.8)
        ax.axvspan(phase_start, phase_end, alpha=0.05, label=phase.name)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Filtered value")
    set_symmetric_ylim_from_max(ax, channel_max_abs)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()

    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"phase_timing_bandpass_{run_dir.name}.png"
        fig.savefig(save_path, dpi=150)
        print(f"Saved graph: {save_path}")

    if show:
        plt.show(block=False)


def plot_phase_folded(
    folded_by_phase: Dict[str, List[FoldedPhase]],
    run_dir: Path,
    frequency_hz: float,
    start_on: Optional[bool],
    save_dir: Optional[Path],
    show: bool,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "matplotlib is required for graph display. "
            "Install it with: conda install matplotlib"
        ) from exc

    period_ms = 1000.0 / frequency_hz
    half_period_ms = period_ms / 2.0
    zero_label = phase_zero_label(start_on)

    for phase_name, folded_list in folded_by_phase.items():
        if not folded_list:
            continue
        phase_max_abs = max_abs_from_arrays(
            array
            for folded in folded_list
            for array in (folded.segments, folded.average_segment)
        )

        row_count = len(folded_list)
        fig, axes = plt.subplots(
            row_count,
            2,
            figsize=(13, max(4, 3.0 * row_count)),
            squeeze=False,
            constrained_layout=True,
        )
        phase = folded_list[0].phase
        fig.suptitle(
            f"Phase timing: {run_dir.name} / {phase_name} "
            f"({phase.start_s:.3f}-{phase.end_s:.3f} s, "
            f"{phase.source}, cycles={phase.cycle_source}, {zero_label})"
        )

        for row_index, folded in enumerate(folded_list):
            color = CHANNEL_COLORS.get(folded.channel)
            waveform_ax = axes[row_index][0]
            peak_ax = axes[row_index][1]

            for segment in folded.segments:
                waveform_ax.plot(
                    folded.phase_axis_ms,
                    segment,
                    color=color,
                    linewidth=DEFAULT_LINE_WIDTH,
                    alpha=DEFAULT_ALPHA,
                )
            waveform_ax.plot(
                folded.phase_axis_ms,
                folded.average_segment,
                color="black",
                linewidth=DEFAULT_AVERAGE_LINE_WIDTH,
                label="average",
            )
            if phase_name == "stimulus":
                if start_on is True:
                    waveform_ax.axvspan(0.0, half_period_ms, color="gold", alpha=0.12, label="ON half")
                elif start_on is False:
                    waveform_ax.axvspan(half_period_ms, period_ms, color="gold", alpha=0.12, label="ON half")
            waveform_ax.axvline(half_period_ms, color="gray", linestyle=":", linewidth=1.0)
            waveform_ax.set_title(
                f"{folded.channel}: folded filtered waveform, cycles={folded.segments.shape[0]}"
            )
            waveform_ax.set_xlabel("Phase time [ms]")
            waveform_ax.set_ylabel("Filtered value")
            waveform_ax.set_xlim(0.0, period_ms)
            set_symmetric_ylim_from_max(waveform_ax, phase_max_abs)
            waveform_ax.grid(True, alpha=0.3)
            waveform_ax.legend(loc="best")

            elapsed_s = folded.cycle_start_s - phase.start_s
            peak_ax.scatter(elapsed_s, folded.peak_time_ms, color=color, s=16, label=folded.channel)
            peak_ax.axhline(half_period_ms, color="gray", linestyle=":", linewidth=1.0)
            peak_ax.set_title(f"{folded.channel}: peak timing flow")
            peak_ax.set_xlabel("Elapsed time in phase [s]")
            peak_ax.set_ylabel("Peak time in cycle [ms]")
            peak_ax.set_ylim(0.0, period_ms)
            peak_ax.grid(True, alpha=0.3)
            peak_ax.legend(loc="best")

        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / f"phase_timing_{run_dir.name}_{phase_name}.png"
            fig.savefig(save_path, dpi=150)
            print(f"Saved graph: {save_path}")

        if show:
            plt.show(block=False)

    if show:
        import matplotlib.pyplot as plt

        plt.show()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase timing checker for bandpass-filtered MAX2 measurement data."
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
        "--frequency",
        type=float,
        default=None,
        help="Stimulus frequency. If omitted, use metadata.json.",
    )
    parser.add_argument(
        "--start-state",
        choices=("metadata", "on", "off"),
        default="metadata",
        help="Stimulus phase zero state. metadata uses metadata.json start_on.",
    )
    parser.add_argument(
        "--phases",
        type=parse_phases,
        default=DEFAULT_PHASES,
        help="Comma-separated phases. Use all for fixation_before,stimulus,fixation_after.",
    )
    parser.add_argument(
        "--channels",
        type=parse_channels,
        default=DEFAULT_CHANNELS,
        help="Comma-separated channels, e.g. ch1,ch2 or ch1,ch2,ch3.",
    )
    parser.add_argument(
        "--filter-a",
        type=lambda text: parse_coefficients(text, DEFAULT_BANDPASS_A),
        default=np.asarray(DEFAULT_BANDPASS_A, dtype=float),
        help="Comma-separated IIR denominator coefficients.",
    )
    parser.add_argument(
        "--filter-b",
        type=lambda text: parse_coefficients(text, DEFAULT_BANDPASS_B),
        default=np.asarray(DEFAULT_BANDPASS_B, dtype=float),
        help="Comma-separated IIR numerator coefficients.",
    )
    parser.add_argument("--no-filter", action="store_true", help="Skip the bandpass filter.")
    parser.add_argument(
        "--notch",
        type=parse_on_off,
        default=False,
        metavar="{on,off}",
        help="Apply the preset IIR notch filter before the bandpass filter.",
    )
    parser.add_argument(
        "--notch-a",
        type=lambda text: parse_coefficients(text, DEFAULT_NOTCH_A),
        default=np.asarray(DEFAULT_NOTCH_A, dtype=float),
        help="Comma-separated IIR notch denominator coefficients.",
    )
    parser.add_argument(
        "--notch-b",
        type=lambda text: parse_coefficients(text, DEFAULT_NOTCH_B),
        default=np.asarray(DEFAULT_NOTCH_B, dtype=float),
        help="Comma-separated IIR notch numerator coefficients.",
    )
    parser.add_argument(
        "--filter-delay-ms",
        type=float,
        default=DEFAULT_FILTER_DELAY_MS,
        help="Manual BPF delay correction. Positive values shift filtered output earlier.",
    )
    parser.add_argument(
        "--peak-mode",
        choices=("max", "min", "abs"),
        default=DEFAULT_PEAK_MODE,
        help="Peak timing mode inside each stimulus period.",
    )
    parser.add_argument(
        "--save",
        nargs="?",
        const="",
        default=None,
        help="Save PNG graphs. If no folder is given, save in the run folder.",
    )
    parser.add_argument("--no-show", action="store_true", help="Do not display graph windows.")
    return parser.parse_args(argv)


def save_directory_from_arg(save_arg: Optional[str], run_dir: Path) -> Optional[Path]:
    if save_arg is None:
        return None
    if save_arg == "":
        return run_dir
    return Path(save_arg).expanduser()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_dir = resolve_run_dir(args.data_path)
    serial_csv = find_required_file(run_dir, SERIAL_CSV_NAME)
    events_csv = find_required_file(run_dir, EVENTS_CSV_NAME)
    frames_csv = find_required_file(run_dir, FRAMES_CSV_NAME)
    metadata = read_metadata(run_dir)

    frequency_hz = args.frequency if args.frequency is not None else metadata.frequency_hz
    if frequency_hz is None:
        raise ValueError("--frequency is required when metadata.json has no stimulus frequency")
    if frequency_hz <= 0.0:
        raise ValueError("--frequency must be greater than 0")

    if args.start_state == "on":
        start_on = True
    elif args.start_state == "off":
        start_on = False
    else:
        start_on = metadata.start_on

    phase_intervals = read_frame_phase_intervals(
        frames_csv=frames_csv,
        phases=args.phases,
        frequency_hz=frequency_hz,
        start_on=start_on,
    )
    if not phase_intervals:
        phase_intervals = read_event_phase_intervals(events_csv, args.phases)
    if not phase_intervals:
        raise RuntimeError(f"No matching phase intervals found for phases={args.phases}")

    series = load_channel_series(serial_csv, args.channels)
    if not series:
        raise RuntimeError("No valid channel data found")

    channels = build_uniform_channels(
        series=series,
        b=args.filter_b,
        a=args.filter_a,
        use_filter=not args.no_filter,
        filter_delay_ms=args.filter_delay_ms,
        notch_b=args.notch_b,
        notch_a=args.notch_a,
        use_notch=args.notch,
    )
    amplitude_reference = max_abs_from_arrays(channel.filtered for channel in channels.values())

    folded_by_phase: Dict[str, List[FoldedPhase]] = {}
    for phase in phase_intervals:
        folded_list: List[FoldedPhase] = []
        for channel_name in args.channels:
            channel = channels.get(channel_name)
            if channel is None:
                continue
            folded = fold_phase_channel(
                phase=phase,
                channel=channel,
                frequency_hz=frequency_hz,
                peak_mode=args.peak_mode,
            )
            if folded is not None:
                folded_list.append(folded)
        folded_by_phase[phase.name] = folded_list

    print(f"Run folder: {run_dir}")
    print(f"Serial CSV: {serial_csv}")
    print(f"Events CSV: {events_csv}")
    print(f"Frames CSV: {frames_csv}")
    print(f"Frequency: {frequency_hz:.6f} Hz, period={1000.0 / frequency_hz:.3f} ms")
    print(f"Phase zero: {phase_zero_label(start_on)}")
    print(f"Bandpass filter: {'OFF' if args.no_filter else 'ON'}")
    print(f"Notch filter: {'ON' if args.notch else 'OFF'}")
    if amplitude_reference is not None:
        print(f"Amplitude reference: {amplitude_reference:.6g} (max abs across selected channels)")
    print(f"Filter delay correction: {args.filter_delay_ms:.3f} ms")
    for phase in phase_intervals:
        print(
            f"{phase.name}: {phase.start_s:.3f}-{phase.end_s:.3f} s "
            f"({phase.duration_s:.3f} s, source={phase.source}, cycles={phase.cycle_source})"
        )
        if phase.cycle_start_s is not None and phase.cycle_start_s.size >= 2:
            cycle_dt_ms = np.diff(phase.cycle_start_s) * 1000.0
            print(
                f"  frame-derived cycle interval: "
                f"mean={float(np.mean(cycle_dt_ms)):.3f} ms, "
                f"std={float(np.std(cycle_dt_ms)):.3f} ms, "
                f"min={float(np.min(cycle_dt_ms)):.3f} ms, "
                f"max={float(np.max(cycle_dt_ms)):.3f} ms"
            )
        for folded in folded_by_phase.get(phase.name, []):
            mean_peak = float(np.mean(folded.peak_time_ms))
            std_peak = float(np.std(folded.peak_time_ms))
            mean_cycle_ms = float(np.mean(folded.cycle_duration_s) * 1000.0)
            std_cycle_ms = float(np.std(folded.cycle_duration_s) * 1000.0)
            print(
                f"  {folded.channel}: cycles={folded.segments.shape[0]}, "
                f"mean_peak={mean_peak:.3f} ms, std={std_peak:.3f} ms, "
                f"cycle_mean={mean_cycle_ms:.3f} ms, cycle_std={std_cycle_ms:.3f} ms"
            )

    save_dir = save_directory_from_arg(args.save, run_dir)
    if save_dir is not None or not args.no_show:
        ordered_channels = [channels[name] for name in args.channels if name in channels]
        plot_bandpass_overview(
            channels=ordered_channels,
            phase_intervals=phase_intervals,
            run_dir=run_dir,
            save_dir=save_dir,
            show=not args.no_show,
        )
        plot_phase_folded(
            folded_by_phase=folded_by_phase,
            run_dir=run_dir,
            frequency_hz=frequency_hz,
            start_on=start_on,
            save_dir=save_dir,
            show=not args.no_show,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
