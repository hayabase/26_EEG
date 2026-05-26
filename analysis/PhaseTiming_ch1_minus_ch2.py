# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import PhaseTiming as phase_timing


DEFAULT_POSITIVE_CHANNEL = "ch1"
DEFAULT_NEGATIVE_CHANNEL = "ch2"
DEFAULT_METRIC_NAME = "ch1_minus_ch2"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase timing checker using the differential signal ch1 - ch2. "
            "Processing, filtering, folding, and plotting follow PhaseTiming.py."
        )
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
        type=phase_timing.parse_phases,
        default=phase_timing.DEFAULT_PHASES,
        help="Comma-separated phases. Use all for fixation_before,stimulus,fixation_after.",
    )
    parser.add_argument(
        "--positive-channel",
        choices=phase_timing.DEFAULT_CHANNELS,
        default=DEFAULT_POSITIVE_CHANNEL,
        help="Positive side of the differential signal.",
    )
    parser.add_argument(
        "--negative-channel",
        choices=phase_timing.DEFAULT_CHANNELS,
        default=DEFAULT_NEGATIVE_CHANNEL,
        help="Negative side of the differential signal.",
    )
    parser.add_argument(
        "--metric-name",
        default=DEFAULT_METRIC_NAME,
        help="Name shown in graphs and logs for the differential signal.",
    )
    parser.add_argument(
        "--filter-a",
        type=lambda text: phase_timing.parse_coefficients(
            text, phase_timing.DEFAULT_BANDPASS_A
        ),
        default=np.asarray(phase_timing.DEFAULT_BANDPASS_A, dtype=float),
        help="Comma-separated IIR denominator coefficients.",
    )
    parser.add_argument(
        "--filter-b",
        type=lambda text: phase_timing.parse_coefficients(
            text, phase_timing.DEFAULT_BANDPASS_B
        ),
        default=np.asarray(phase_timing.DEFAULT_BANDPASS_B, dtype=float),
        help="Comma-separated IIR numerator coefficients.",
    )
    parser.add_argument("--no-filter", action="store_true", help="Skip the bandpass filter.")
    parser.add_argument(
        "--filter-delay-ms",
        type=float,
        default=phase_timing.DEFAULT_FILTER_DELAY_MS,
        help="Manual BPF delay correction. Positive values shift filtered output earlier.",
    )
    parser.add_argument(
        "--peak-mode",
        choices=("max", "min", "abs"),
        default=phase_timing.DEFAULT_PEAK_MODE,
        help="Peak timing mode inside each stimulus period.",
    )
    parser.add_argument(
        "--save",
        nargs="?",
        const="",
        default=None,
        help=(
            "Save PNG graphs. If no folder is given, save in "
            "<run folder>/phase_timing_ch1_minus_ch2."
        ),
    )
    parser.add_argument("--no-show", action="store_true", help="Do not display graph windows.")
    return parser.parse_args(argv)


def load_difference_series(
    serial_csv: Path,
    positive_channel: str,
    negative_channel: str,
    metric_name: str,
) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    times: List[float] = []
    values: List[float] = []

    for row in phase_timing.iter_valid_rows(serial_csv):
        try:
            time_s = float(row["experiment_time_s"])
            positive_value = float(row.get(positive_channel, ""))
            negative_value = float(row.get(negative_channel, ""))
        except (KeyError, TypeError, ValueError):
            continue
        times.append(time_s)
        values.append(positive_value - negative_value)

    if not values:
        return {}

    return {
        metric_name: (
            np.asarray(times, dtype=float),
            np.asarray(values, dtype=float),
        )
    }


def save_directory_from_arg(save_arg: Optional[str], run_dir: Path) -> Optional[Path]:
    if save_arg is None:
        return None
    if save_arg == "":
        return run_dir / "phase_timing_ch1_minus_ch2"
    return Path(save_arg).expanduser()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.positive_channel == args.negative_channel:
        raise ValueError("--positive-channel and --negative-channel must be different")

    run_dir = phase_timing.resolve_run_dir(args.data_path)
    serial_csv = phase_timing.find_required_file(run_dir, phase_timing.SERIAL_CSV_NAME)
    events_csv = phase_timing.find_required_file(run_dir, phase_timing.EVENTS_CSV_NAME)
    frames_csv = phase_timing.find_required_file(run_dir, phase_timing.FRAMES_CSV_NAME)
    metadata = phase_timing.read_metadata(run_dir)

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

    phase_intervals = phase_timing.read_frame_phase_intervals(
        frames_csv=frames_csv,
        phases=args.phases,
        frequency_hz=frequency_hz,
        start_on=start_on,
    )
    if not phase_intervals:
        phase_intervals = phase_timing.read_event_phase_intervals(events_csv, args.phases)
    if not phase_intervals:
        raise RuntimeError(f"No matching phase intervals found for phases={args.phases}")

    series = load_difference_series(
        serial_csv=serial_csv,
        positive_channel=args.positive_channel,
        negative_channel=args.negative_channel,
        metric_name=args.metric_name,
    )
    if not series:
        raise RuntimeError(
            f"No valid rows found for {args.positive_channel} - {args.negative_channel}"
        )

    channels = phase_timing.build_uniform_channels(
        series=series,
        b=args.filter_b,
        a=args.filter_a,
        use_filter=not args.no_filter,
        filter_delay_ms=args.filter_delay_ms,
    )

    folded_by_phase: Dict[str, List[phase_timing.FoldedPhase]] = {}
    metric_channel = channels.get(args.metric_name)
    if metric_channel is None:
        raise RuntimeError(f"Metric channel not built: {args.metric_name}")

    for phase in phase_intervals:
        folded = phase_timing.fold_phase_channel(
            phase=phase,
            channel=metric_channel,
            frequency_hz=frequency_hz,
            peak_mode=args.peak_mode,
        )
        folded_by_phase[phase.name] = [] if folded is None else [folded]

    phase_timing.CHANNEL_COLORS[args.metric_name] = "tab:purple"

    print(f"Run folder: {run_dir}")
    print(f"Serial CSV: {serial_csv}")
    print(f"Events CSV: {events_csv}")
    print(f"Frames CSV: {frames_csv}")
    print(
        f"Metric: {args.metric_name} = "
        f"{args.positive_channel} - {args.negative_channel}"
    )
    print(f"Frequency: {frequency_hz:.6f} Hz, period={1000.0 / frequency_hz:.3f} ms")
    print(f"Phase zero: {phase_timing.phase_zero_label(start_on)}")
    print(f"Bandpass filter: {'OFF' if args.no_filter else 'ON'}")
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
        phase_timing.plot_bandpass_overview(
            channels=[metric_channel],
            phase_intervals=phase_intervals,
            run_dir=run_dir,
            save_dir=save_dir,
            show=not args.no_show,
        )
        phase_timing.plot_phase_folded(
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
