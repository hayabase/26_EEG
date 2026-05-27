#!/usr/bin/env python
"""
通過域と遷移域を直接指定してFIRバンドパスフィルタを設計するスクリプト.

design_bandpass_filter.py はIIR BPF設計用.
このファイルはFIR BPF設計用で, IIRの極配置ではなく,
タップ数, 線形位相, 一定群遅延, インパルス応答を確認する.

例:
  python filter_design\\design_fir_bandpass_filter.py --passband 9.5,10.5 --transition 3.0
  python filter_design\\design_fir_bandpass_filter.py --numtaps 751 --methods firwin_kaiser,remez
  python filter_design\\design_fir_bandpass_filter.py 9.5 10.5 --tap-counts 501,751,1001 --no-show
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


# ===== 初期値設定 =====

DEFAULT_SAMPLERATE = 1000.0
DEFAULT_PASS_LOW = 9.5
DEFAULT_PASS_HIGH = 10.5
DEFAULT_TRANSITION_LOW = 3.0
DEFAULT_TRANSITION_HIGH = 3.0
DEFAULT_METHODS = "all"
DEFAULT_GPASS = 1.0
DEFAULT_GSTOP = 40.0
DEFAULT_SPEC_TOLERANCE_DB = 0.5
DEFAULT_MAX_NUMTAPS = 3001
DEFAULT_TAP_MULTIPLIERS = "0.75,1.0,1.25"
DEFAULT_WOR_N = 16000
DEFAULT_PLOT_MAX_FREQ = 60.0
DEFAULT_DURATION_MS = 3000.0
DEFAULT_SETTLE_MS = 500.0
DEFAULT_PLOT_WINDOW_MS = 500.0
DEFAULT_DPI = 150


SUPPORTED_METHODS = (
    "firwin_hamming",
    "firwin_hann",
    "firwin_blackman",
    "firwin_kaiser",
    "remez",
    "firls",
)


@dataclass(frozen=True)
class BandpassSpec:
    samplerate: float
    pass_low: float
    pass_high: float
    transition_low: float
    transition_high: float
    stop_low: float
    stop_high: float
    gpass: float
    gstop: float

    @property
    def center_freq(self) -> float:
        return (self.pass_low + self.pass_high) / 2.0

    @property
    def pass_width(self) -> float:
        return self.pass_high - self.pass_low

    @property
    def min_transition(self) -> float:
        return min(self.transition_low, self.transition_high)


@dataclass(frozen=True)
class FirCandidate:
    method: str
    window: str
    numtaps: int
    order: int
    b: np.ndarray
    a: np.ndarray
    frequencies: np.ndarray
    response_db: np.ndarray
    delay_freq: np.ndarray
    delay_ms: np.ndarray
    passband_min_db: float
    passband_max_db: float
    stopband_max_db: float
    center_gain_db: float
    center_delay_ms: float
    bandwidth_3db: float
    q_value: float
    symmetry_error: float
    linear_phase: bool
    passband_violation_db: float
    stopband_violation_db: float
    meets_spec: bool

    def to_json_dict(self, spec: BandpassSpec) -> dict:
        return {
            "method": self.method,
            "window": self.window,
            "numtaps": self.numtaps,
            "order": self.order,
            "a": [float(value) for value in self.a],
            "b": [float(value) for value in self.b],
            "fp": [spec.pass_low, spec.pass_high],
            "fs": [spec.stop_low, spec.stop_high],
            "gpass": spec.gpass,
            "gstop": spec.gstop,
            "q_value": self.q_value,
            "gain_at_target_db": self.center_gain_db,
            "target_delay_ms": self.center_delay_ms,
            "bandwidth_3db": self.bandwidth_3db,
            "passband_min_db": self.passband_min_db,
            "passband_max_db": self.passband_max_db,
            "stopband_max_db": self.stopband_max_db,
            "symmetry_error": self.symmetry_error,
            "linear_phase": self.linear_phase,
            "passband_violation_db": self.passband_violation_db,
            "stopband_violation_db": self.stopband_violation_db,
            "meets_spec": self.meets_spec,
        }


def require_scipy_signal():
    try:
        from scipy import signal
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "scipy が見つからないためFIRフィルタ設計を実行できない. "
            "conda env update -f environment.yml --prune を実行."
        ) from exc
    return signal


def parse_float_list(text: str | None) -> list[float]:
    if text is None:
        return []
    values: list[float] = []
    for raw in text.replace(";", ",").split(","):
        raw = raw.strip()
        if raw:
            values.append(float(raw))
    return values


def parse_int_list(text: str | None) -> list[int]:
    if text is None:
        return []
    values: list[int] = []
    for raw in text.replace(";", ",").split(","):
        raw = raw.strip()
        if raw:
            values.append(int(raw))
    return values


def parse_pair(text: str, label: str) -> tuple[float, float]:
    values = parse_float_list(text)
    if len(values) != 2:
        raise argparse.ArgumentTypeError(f"{label} は 'low,high' の2値で指定")
    return values[0], values[1]


def parse_methods(text: str) -> list[str]:
    text = (text or "").strip().lower()
    if text in ("", "all"):
        return list(SUPPORTED_METHODS)

    methods = [item.strip().lower() for item in text.replace(";", ",").split(",") if item.strip()]
    unknown = [method for method in methods if method not in SUPPORTED_METHODS]
    if unknown:
        raise argparse.ArgumentTypeError(f"未対応のmethod: {', '.join(unknown)}")
    return methods


def make_odd_numtaps(value: int) -> int:
    value = max(3, int(value))
    return value if value % 2 == 1 else value + 1


def max_numtaps_from_delay_ms(max_delay_ms: float | None, samplerate: float) -> int | None:
    if max_delay_ms is None:
        return None
    if max_delay_ms < 0:
        raise ValueError("--max-delay-ms は0以上にする")
    raw = int(math.floor((max_delay_ms / 1000.0) * samplerate * 2.0 + 1.0 + 1e-9))
    if raw % 2 == 0:
        raw -= 1
    return raw


def ask_text(label: str, default):
    prompt = f"{label} [{default}]: " if default is not None else f"{label}: "
    value = input(prompt).strip()
    return default if value == "" else value


def ask_float(label: str, default: float) -> float:
    while True:
        value = ask_text(label, default)
        try:
            return float(value)
        except ValueError:
            print("数値で入力.")


def apply_interactive_inputs(args: argparse.Namespace) -> None:
    print("FIR BPF設計, Enterで既定値を使用.")
    args.samplerate = ask_float("サンプリング周波数[Hz]", args.samplerate)
    args.pass_low = ask_float("通過域下限[Hz]", args.pass_low or DEFAULT_PASS_LOW)
    args.pass_high = ask_float("通過域上限[Hz]", args.pass_high or DEFAULT_PASS_HIGH)
    args.transition_low = ask_float("低周波側の遷移域幅[Hz]", args.transition_low)
    args.transition_high = ask_float("高周波側の遷移域幅[Hz]", args.transition_high)
    args.methods = ask_text("設計法 all/firwin_hamming/firwin_kaiser/remez/firls", args.methods)
    args.gpass = ask_float("通過域端最大損失[dB]", args.gpass)
    args.gstop = ask_float("阻止域端最小減衰[dB]", args.gstop)
    show_text = ask_text("グラフを表示する [Y/n]", "Y").lower()
    if show_text in ("n", "no"):
        args.no_show = True


def build_spec(args: argparse.Namespace) -> BandpassSpec:
    pass_low = args.pass_low
    pass_high = args.pass_high

    if args.passband:
        pass_low, pass_high = parse_pair(args.passband, "--passband")
    if pass_low is None:
        pass_low = DEFAULT_PASS_LOW
    if pass_high is None:
        pass_high = DEFAULT_PASS_HIGH

    transition_low = args.transition_low
    transition_high = args.transition_high
    if args.transition:
        values = parse_float_list(args.transition)
        if len(values) == 1:
            transition_low = values[0]
            transition_high = values[0]
        elif len(values) == 2:
            transition_low, transition_high = values
        else:
            raise ValueError("--transition は 'width' または 'low,high' で指定")

    spec = BandpassSpec(
        samplerate=float(args.samplerate),
        pass_low=float(pass_low),
        pass_high=float(pass_high),
        transition_low=float(transition_low),
        transition_high=float(transition_high),
        stop_low=float(pass_low - transition_low),
        stop_high=float(pass_high + transition_high),
        gpass=float(args.gpass),
        gstop=float(args.gstop),
    )
    validate_spec(spec)
    return spec


def validate_spec(spec: BandpassSpec) -> None:
    nyquist = spec.samplerate / 2.0
    if spec.samplerate <= 0:
        raise ValueError("samplerate は正の値")
    if spec.pass_low <= 0 or spec.pass_high <= 0:
        raise ValueError("通過域は0Hzより大きくする")
    if spec.pass_low >= spec.pass_high:
        raise ValueError("pass_low は pass_high より小さくする")
    if spec.transition_low <= 0 or spec.transition_high <= 0:
        raise ValueError("transition は正の値")
    if spec.stop_low <= 0:
        raise ValueError("stop_low が0Hz以下. pass_low または transition_low を見直す")
    if spec.stop_high >= nyquist:
        raise ValueError(f"stop_high はナイキスト周波数 {nyquist:g} Hz 未満にする")
    if spec.gpass <= 0 or spec.gstop <= 0:
        raise ValueError("gpass, gstop は正の値")
    if spec.gstop <= spec.gpass:
        raise ValueError("gstop は gpass より大きくする")


def transition_weights(spec: BandpassSpec) -> list[float]:
    delta_pass = max(10.0 ** (spec.gpass / 20.0) - 1.0, 1e-6)
    delta_stop = max(10.0 ** (-spec.gstop / 20.0), 1e-9)
    pass_weight = 1.0 / delta_pass
    stop_weight = 1.0 / delta_stop
    return [stop_weight, pass_weight, stop_weight]


def estimate_numtaps(spec: BandpassSpec) -> tuple[int, float]:
    signal = require_scipy_signal()
    nyquist = spec.samplerate / 2.0
    width = spec.min_transition / nyquist
    numtaps, beta = signal.kaiserord(spec.gstop, width)
    return make_odd_numtaps(numtaps), float(beta)


def candidate_numtaps(args: argparse.Namespace, spec: BandpassSpec) -> list[int]:
    if args.tap_counts:
        raw_counts = parse_int_list(args.tap_counts)
    elif args.numtaps is not None:
        raw_counts = [args.numtaps]
    else:
        estimated, _beta = estimate_numtaps(spec)
        multipliers = parse_float_list(args.tap_multipliers)
        raw_counts = [int(round(estimated * multiplier)) for multiplier in multipliers]
        raw_counts.append(estimated)

    max_by_delay = max_numtaps_from_delay_ms(args.max_delay_ms, spec.samplerate)
    max_allowed = args.max_numtaps if max_by_delay is None else min(args.max_numtaps, max_by_delay)
    if max_allowed < 3:
        return []

    counts = sorted({make_odd_numtaps(count) for count in raw_counts})
    usable = [count for count in counts if count <= max_allowed]
    if not usable and counts:
        usable = [max_allowed]
    return usable


def design_fir(method: str, numtaps: int, spec: BandpassSpec) -> tuple[str, np.ndarray, np.ndarray]:
    signal = require_scipy_signal()
    nyquist = spec.samplerate / 2.0
    bands = [0.0, spec.stop_low, spec.pass_low, spec.pass_high, spec.stop_high, nyquist]
    weights = transition_weights(spec)
    a = np.asarray([1.0], dtype=float)

    if method.startswith("firwin_"):
        window_name = method.removeprefix("firwin_")
        if window_name == "kaiser":
            _estimated, beta = estimate_numtaps(spec)
            window = ("kaiser", beta)
            window_label = f"kaiser(beta={beta:.3g})"
        else:
            window = window_name
            window_label = window_name
        b = signal.firwin(
            numtaps,
            [spec.pass_low, spec.pass_high],
            pass_zero=False,
            window=window,
            scale=True,
            fs=spec.samplerate,
        )
        return window_label, np.asarray(b, dtype=float), a

    if method == "remez":
        b = signal.remez(
            numtaps,
            bands,
            [0.0, 1.0, 0.0],
            weight=weights,
            fs=spec.samplerate,
            maxiter=100,
        )
        return "equiripple", np.asarray(b, dtype=float), a

    if method == "firls":
        b = signal.firls(
            numtaps,
            bands,
            [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
            weight=weights,
            fs=spec.samplerate,
        )
        return "least_squares", np.asarray(b, dtype=float), a

    raise ValueError(f"未対応method: {method}")


def db_to_amplitude_ratio(db_values):
    return np.power(10.0, np.asarray(db_values, dtype=float) / 20.0)


def format_amplitude_ratio(value: float) -> str:
    if not np.isfinite(value):
        return ""
    if value >= 10.0:
        return f"{value:.1f}x"
    if value >= 1.0:
        return f"{value:.3g}x"
    if value >= 0.001:
        return f"{value:.3f}x"
    return f"{value:.1e}x"


def sync_ratio_axis_to_db_axis(db_axis, ratio_axis):
    ymin, ymax = db_axis.get_ylim()
    lower, upper = sorted((ymin, ymax))
    ticks = [float(tick) for tick in db_axis.get_yticks() if lower <= float(tick) <= upper]
    ratio_axis.set_ylim(ymin, ymax)
    ratio_axis.set_yticks(ticks)
    ratio_axis.set_yticklabels(
        [format_amplitude_ratio(float(db_to_amplitude_ratio(tick))) for tick in ticks]
    )


def compute_frequency_response(b: np.ndarray, a: np.ndarray, samplerate: float, wor_n: int):
    signal = require_scipy_signal()
    w, h = signal.freqz(b, a, worN=wor_n)
    frequencies = w / (2.0 * np.pi) * samplerate
    magnitude = np.maximum(np.abs(h), 1e-20)
    response_db = 20.0 * np.log10(magnitude)
    return frequencies, response_db


def compute_group_delay(numtaps: int, samplerate: float, wor_n: int):
    delay_samples = (numtaps - 1) / 2.0
    delay_ms_value = delay_samples / samplerate * 1000.0
    delay_freq = np.linspace(0.0, samplerate / 2.0, wor_n, endpoint=False)
    delay_ms = np.full_like(delay_freq, delay_ms_value, dtype=float)
    return delay_freq, delay_ms


def compute_3db_bandwidth(frequencies: np.ndarray, response_db: np.ndarray, center_freq: float):
    if frequencies.size == 0:
        return 0.0
    center_index = int(np.argmin(np.abs(frequencies - center_freq)))
    threshold = response_db[center_index] - 3.0
    valid = np.isfinite(response_db) & (response_db >= threshold)

    left = center_index
    while left > 0 and valid[left - 1]:
        left -= 1
    right = center_index
    while right < len(valid) - 1 and valid[right + 1]:
        right += 1

    return float(frequencies[right] - frequencies[left])


def nearest_value(x_values: np.ndarray, y_values: np.ndarray, x: float) -> float:
    index = int(np.argmin(np.abs(x_values - x)))
    return float(y_values[index])


def evaluate_candidate(
    method: str,
    window: str,
    b: np.ndarray,
    a: np.ndarray,
    spec: BandpassSpec,
    wor_n: int,
    tolerance_db: float,
) -> FirCandidate:
    frequencies, response_db = compute_frequency_response(b, a, spec.samplerate, wor_n)
    delay_freq, delay_ms = compute_group_delay(len(b), spec.samplerate, wor_n)

    pass_mask = (frequencies >= spec.pass_low) & (frequencies <= spec.pass_high)
    stop_mask = (frequencies <= spec.stop_low) | (frequencies >= spec.stop_high)
    stop_mask &= frequencies <= (spec.samplerate / 2.0)

    passband_min_db = float(np.min(response_db[pass_mask])) if np.any(pass_mask) else float("nan")
    passband_max_db = float(np.max(response_db[pass_mask])) if np.any(pass_mask) else float("nan")
    stopband_max_db = float(np.max(response_db[stop_mask])) if np.any(stop_mask) else float("nan")
    center_gain_db = nearest_value(frequencies, response_db, spec.center_freq)
    center_delay_ms = float((len(b) - 1) / 2.0 / spec.samplerate * 1000.0)
    bandwidth_3db = compute_3db_bandwidth(frequencies, response_db, spec.center_freq)
    q_value = spec.center_freq / bandwidth_3db if bandwidth_3db > 0 else float("inf")
    symmetry_error = float(np.max(np.abs(b - b[::-1]))) if b.size else float("inf")
    linear_phase = symmetry_error <= 1e-10
    pass_limit = -spec.gpass - tolerance_db
    stop_limit = -spec.gstop + tolerance_db
    passband_violation_db = max(0.0, pass_limit - passband_min_db)
    stopband_violation_db = max(0.0, stopband_max_db - stop_limit)
    meets_spec = bool(passband_violation_db == 0.0 and stopband_violation_db == 0.0)

    return FirCandidate(
        method=method,
        window=window,
        numtaps=len(b),
        order=len(b) - 1,
        b=b,
        a=a,
        frequencies=frequencies,
        response_db=response_db,
        delay_freq=delay_freq,
        delay_ms=delay_ms,
        passband_min_db=passband_min_db,
        passband_max_db=passband_max_db,
        stopband_max_db=stopband_max_db,
        center_gain_db=center_gain_db,
        center_delay_ms=center_delay_ms,
        bandwidth_3db=bandwidth_3db,
        q_value=q_value,
        symmetry_error=symmetry_error,
        linear_phase=linear_phase,
        passband_violation_db=passband_violation_db,
        stopband_violation_db=stopband_violation_db,
        meets_spec=meets_spec,
    )


def sort_candidates(candidates: list[FirCandidate]) -> list[FirCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            not item.meets_spec,
            item.passband_violation_db + item.stopband_violation_db,
            item.numtaps,
            -item.q_value if np.isfinite(item.q_value) else float("-inf"),
            item.stopband_max_db,
            abs(item.center_gain_db),
        ),
    )


def build_candidates(args: argparse.Namespace, spec: BandpassSpec) -> list[FirCandidate]:
    methods = parse_methods(args.methods)
    taps = candidate_numtaps(args, spec)
    candidates: list[FirCandidate] = []

    for numtaps in taps:
        for method in methods:
            try:
                window, b, a = design_fir(method, numtaps, spec)
                candidate = evaluate_candidate(
                    method=method,
                    window=window,
                    b=b,
                    a=a,
                    spec=spec,
                    wor_n=args.wor_n,
                    tolerance_db=args.spec_tolerance_db,
                )
            except Exception as exc:
                print(f"{method}, numtaps={numtaps}: 設計スキップ: {exc}")
                continue
            candidates.append(candidate)
    return sort_candidates(candidates)


def format_tuple(values: Iterable[float], indent: str = "    ") -> str:
    lines = [f"{indent}{float(value):.18g}," for value in values]
    return "(\n" + "\n".join(lines) + "\n)"


def print_spec(spec: BandpassSpec, args: argparse.Namespace) -> None:
    estimated, beta = estimate_numtaps(spec)
    max_by_delay = max_numtaps_from_delay_ms(args.max_delay_ms, spec.samplerate)
    print("FIR BPF specification:")
    print(f"  samplerate: {spec.samplerate:g} Hz")
    print(f"  passband: {spec.pass_low:g} Hz - {spec.pass_high:g} Hz")
    print(f"  transition low/high: {spec.transition_low:g} Hz / {spec.transition_high:g} Hz")
    print(f"  stopband: <= {spec.stop_low:g} Hz, >= {spec.stop_high:g} Hz")
    print(f"  gpass: {spec.gpass:g} dB")
    print(f"  gstop: {spec.gstop:g} dB")
    if args.max_delay_ms is not None:
        if max_by_delay is None or max_by_delay < 3:
            print(f"  max delay: {args.max_delay_ms:g} ms (no usable FIR numtaps >= 3)")
        else:
            print(f"  max delay: {args.max_delay_ms:g} ms (numtaps <= {max_by_delay})")
    print(f"  kaiser estimated numtaps: {estimated} (beta={beta:.3g})")
    print(f"  candidate numtaps: {', '.join(str(value) for value in candidate_numtaps(args, spec))}")
    print()


def print_one_candidate(rank: int, candidate: FirCandidate, spec: BandpassSpec) -> None:
    print(f"Rank {rank}:")
    print(f"  method: {candidate.method}")
    print(f"  window: {candidate.window}")
    print(f"  numtaps: {candidate.numtaps}")
    print(f"  order: {candidate.order}")
    print(f"  passband gain min/max: {candidate.passband_min_db:.2f} / {candidate.passband_max_db:.2f} dB")
    print(f"  stopband max gain: {candidate.stopband_max_db:.2f} dB")
    print(f"  gain_at_{spec.center_freq:g}Hz: {candidate.center_gain_db:.2f} dB")
    print(f"  constant group delay: {candidate.center_delay_ms:.2f} ms")
    print(f"  bandwidth_3db: {candidate.bandwidth_3db:.4f} Hz")
    print(f"  Q: {candidate.q_value:.2f}")
    print(f"  symmetry_error: {candidate.symmetry_error:.3e}")
    print(f"  linear_phase: {'yes' if candidate.linear_phase else 'no'}")
    print(f"  meets_spec: {'yes' if candidate.meets_spec else 'no'}")
    if not candidate.meets_spec:
        print(
            f"  violations: passband={candidate.passband_violation_db:.2f} dB, "
            f"stopband={candidate.stopband_violation_db:.2f} dB"
        )
    print()


def print_coefficients(label: str, candidate: FirCandidate) -> None:
    print(f"{label}_BANDPASS_A = " + format_tuple(candidate.a))
    print()
    print(f"{label}_BANDPASS_B = " + format_tuple(candidate.b))
    print()


def print_candidates(candidates: list[FirCandidate], spec: BandpassSpec, print_mode: str) -> None:
    if not candidates:
        print("FIRフィルタ候補なし.")
        print("max_numtapsを増やす, transitionを広げる, gstopを下げる.")
        return

    for rank, candidate in enumerate(candidates, start=1):
        print_one_candidate(rank, candidate, spec)

    if print_mode == "none":
        return
    if print_mode == "all":
        print("ランキング全ての係数 a,b:")
        for rank, candidate in enumerate(candidates, start=1):
            print(
                f"# Rank {rank}: method={candidate.method}, "
                f"window={candidate.window}, numtaps={candidate.numtaps}"
            )
            print_coefficients(f"RANK_{rank}_FIR", candidate)
        return

    print("PhaseTiming.py へ貼る係数(Rank 1):")
    print_coefficients("DEFAULT", candidates[0])


def save_candidates(path: Path, args: argparse.Namespace, spec: BandpassSpec, candidates: list[FirCandidate]) -> None:
    payload = {
        "settings": {
            "samplerate": spec.samplerate,
            "passband": [spec.pass_low, spec.pass_high],
            "transition": [spec.transition_low, spec.transition_high],
            "stopband": [spec.stop_low, spec.stop_high],
            "gpass": spec.gpass,
            "gstop": spec.gstop,
            "methods": parse_methods(args.methods),
            "numtaps": candidate_numtaps(args, spec),
            "max_delay_ms": args.max_delay_ms,
            "spec_tolerance_db": args.spec_tolerance_db,
            "fir_note": "FIR denominator is a=[1.0]. There are no recursive IIR poles.",
        },
        "candidates": [candidate.to_json_dict(spec) for candidate in candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON saved: {path}")


def shade_filter_regions(ax, spec: BandpassSpec) -> None:
    ax.axvspan(0.0, spec.stop_low, color="0.9", alpha=0.35, label="Stopband")
    ax.axvspan(spec.stop_low, spec.pass_low, color="#ffcc66", alpha=0.25, label="Transition")
    ax.axvspan(spec.pass_low, spec.pass_high, color="#81c784", alpha=0.18, label="Passband")
    ax.axvspan(spec.pass_high, spec.stop_high, color="#ffcc66", alpha=0.25)
    ax.axvspan(spec.stop_high, spec.samplerate / 2.0, color="0.9", alpha=0.20)
    for value in (spec.stop_low, spec.pass_low, spec.pass_high, spec.stop_high):
        ax.axvline(value, color="0.35", linestyle=":", linewidth=0.8)


def build_test_signal(spec: BandpassSpec, args: argparse.Namespace):
    signal = require_scipy_signal()
    test_freqs = parse_float_list(args.test_freqs) if args.test_freqs else [
        spec.stop_low,
        spec.center_freq,
        spec.stop_high,
    ]
    test_amps = parse_float_list(args.test_amps) if args.test_amps else [0.5, 1.0, 0.5]
    if len(test_amps) != len(test_freqs):
        raise ValueError("--test-freqs と --test-amps の個数を一致させる")

    duration_sec = args.duration_ms / 1000.0
    sample_count = int(round(duration_sec * spec.samplerate))
    if sample_count <= 1:
        raise ValueError("duration-ms が短すぎる")
    time_sec = np.arange(sample_count, dtype=float) / spec.samplerate
    x = np.zeros_like(time_sec)
    for freq, amp in zip(test_freqs, test_amps):
        x += amp * np.sin(2.0 * np.pi * freq * time_sec)
    return signal, time_sec, x, test_freqs


def enable_candidate_check_panel(fig, candidate_artists: dict[int, list], labels: list[str]) -> None:
    try:
        from matplotlib.widgets import CheckButtons
    except Exception:
        return

    if not candidate_artists:
        return

    fig.subplots_adjust(left=0.20, right=0.96, bottom=0.08, top=0.93, wspace=0.28, hspace=0.36)
    panel_height = min(0.42, 0.045 * len(labels) + 0.08)
    ax_box = fig.add_axes([0.015, 0.94 - panel_height, 0.16, panel_height])
    buttons = CheckButtons(ax_box, labels, [True] * len(labels))
    ax_box.set_title("Show", fontsize=9)

    def on_clicked(label: str) -> None:
        try:
            index = labels.index(label)
        except ValueError:
            return
        artists = candidate_artists.get(index, [])
        if not artists:
            return
        visible = not artists[0].get_visible()
        for artist in artists:
            artist.set_visible(visible)
        fig.canvas.draw_idle()

    buttons.on_clicked(on_clicked)
    fig._candidate_check_buttons = buttons


def plot_candidates(candidates: list[FirCandidate], spec: BandpassSpec, args: argparse.Namespace) -> None:
    if not candidates:
        return
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib が見つからないためグラフ表示をスキップ.")
        return

    signal, time_sec, x, _test_freqs = build_test_signal(spec, args)
    plot_start_sec = args.settle_ms / 1000.0
    plot_end_sec = min(args.duration_ms / 1000.0, plot_start_sec + args.plot_window_ms / 1000.0)
    time_mask = (time_sec >= plot_start_sec) & (time_sec <= plot_end_sec)

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    ax_response = axes[0, 0]
    ax_delay = axes[1, 0]
    ax_taps = axes[0, 1]
    ax_time = axes[1, 1]
    fig.suptitle("FIR bandpass filter design: response / constant delay / taps / time waveform")

    candidate_artists: dict[int, list] = {}
    labels: list[str] = []

    shade_filter_regions(ax_response, spec)
    shade_filter_regions(ax_delay, spec)
    ax_response.axhline(-spec.gpass, color="green", linestyle="--", linewidth=1.0, label=f"-gpass ({-spec.gpass:g} dB)")
    ax_response.axhline(-spec.gstop, color="red", linestyle="--", linewidth=1.0, label=f"-gstop ({-spec.gstop:g} dB)")
    ax_time.plot(time_sec[time_mask] * 1000.0, x[time_mask], color="0.25", linewidth=1.2, label="Input")

    for rank, candidate in enumerate(candidates, start=1):
        label = f"R{rank} {candidate.method} N{candidate.numtaps}"
        labels.append(label)
        artists = []

        freq_mask = candidate.frequencies <= args.plot_max_freq
        line_response, = ax_response.plot(
            candidate.frequencies[freq_mask],
            candidate.response_db[freq_mask],
            linewidth=1.4,
            label=(
                f"{label}, Q={candidate.q_value:.1f}, "
                f"delay={candidate.center_delay_ms:.1f} ms"
            ),
        )
        artists.append(line_response)

        delay_mask = candidate.delay_freq <= args.plot_max_freq
        line_delay, = ax_delay.plot(
            candidate.delay_freq[delay_mask],
            candidate.delay_ms[delay_mask],
            linewidth=1.4,
            label=f"{label}, delay={candidate.center_delay_ms:.1f} ms",
        )
        artists.append(line_delay)

        tap_indices = np.arange(candidate.numtaps)
        line_taps, = ax_taps.plot(
            tap_indices,
            candidate.b,
            linewidth=1.0,
            label=label,
        )
        artists.append(line_taps)

        y = signal.lfilter(candidate.b, candidate.a, x)
        line_time, = ax_time.plot(
            time_sec[time_mask] * 1000.0,
            y[time_mask],
            linewidth=1.2,
            label=label,
        )
        artists.append(line_time)
        candidate_artists[rank - 1] = artists

    ax_response.axvline(spec.center_freq, color="black", linestyle="-.", linewidth=1.0, label="center")
    ax_response.set_title("Frequency response")
    ax_response.set_xlabel("Frequency [Hz]")
    ax_response.set_ylabel("Gain [dB]")
    ax_response.set_xlim(0.0, args.plot_max_freq)
    ax_response.grid(True)
    ax_response.legend(loc="best", fontsize=8)

    ratio_axis = ax_response.twinx()
    ratio_axis.set_ylabel("Amplitude ratio")
    sync_ratio_axis_to_db_axis(ax_response, ratio_axis)
    ax_response.callbacks.connect(
        "ylim_changed",
        lambda axis: sync_ratio_axis_to_db_axis(axis, ratio_axis),
    )

    ax_delay.axvline(spec.center_freq, color="black", linestyle="-.", linewidth=1.0)
    ax_delay.set_title("FIR group delay")
    ax_delay.set_xlabel("Frequency [Hz]")
    ax_delay.set_ylabel("Delay [ms]")
    ax_delay.set_xlim(0.0, args.plot_max_freq)
    if args.delay_ylim:
        ymin, ymax = parse_pair(args.delay_ylim, "--delay-ylim")
        ax_delay.set_ylim(ymin, ymax)
    ax_delay.grid(True)
    ax_delay.legend(loc="best", fontsize=8)

    ax_taps.set_title("FIR impulse response / taps")
    ax_taps.set_xlabel("Tap index")
    ax_taps.set_ylabel("Coefficient b[n]")
    ax_taps.grid(True)
    ax_taps.legend(loc="best", fontsize=8)

    ax_time.set_title("Time waveform")
    ax_time.set_xlabel("Time [ms]")
    ax_time.set_ylabel("Amplitude")
    ax_time.grid(True)
    ax_time.legend(loc="best", fontsize=8)

    enable_candidate_check_panel(fig, candidate_artists, labels)

    if args.save_figure:
        save_path = Path(args.save_figure)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=args.dpi)
        print(f"Figure saved: {save_path}")

    if not args.no_show:
        plt.show(block=True)
    else:
        plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通過域と遷移域を直接指定してFIRバンドパスフィルタを設計する."
    )
    parser.add_argument("pass_low_pos", nargs="?", type=float, help="省略指定: 通過域下限[Hz]")
    parser.add_argument("pass_high_pos", nargs="?", type=float, help="省略指定: 通過域上限[Hz]")
    parser.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--passband", help="通過域. 例: 9.5,10.5")
    parser.add_argument("--pass-low", dest="pass_low", type=float, default=None)
    parser.add_argument("--pass-high", dest="pass_high", type=float, default=None)
    parser.add_argument("--transition", help="遷移域幅. 例: 3.0 または 2.0,3.0")
    parser.add_argument("--transition-low", type=float, default=DEFAULT_TRANSITION_LOW)
    parser.add_argument("--transition-high", type=float, default=DEFAULT_TRANSITION_HIGH)
    parser.add_argument("--methods", default=DEFAULT_METHODS, help="all, firwin_hamming, firwin_hann, firwin_blackman, firwin_kaiser, remez, firls")
    parser.add_argument("--gpass", type=float, default=DEFAULT_GPASS)
    parser.add_argument("--gstop", type=float, default=DEFAULT_GSTOP)
    parser.add_argument("--spec-tolerance-db", type=float, default=DEFAULT_SPEC_TOLERANCE_DB)
    parser.add_argument("--numtaps", type=int, default=None, help="単一のFIRタップ数. 偶数指定時は奇数へ丸める")
    parser.add_argument("--tap-counts", help="比較するFIRタップ数. 例: 501,751,1001")
    parser.add_argument("--tap-multipliers", default=DEFAULT_TAP_MULTIPLIERS, help="Kaiser推定タップ数への倍率. numtaps/tap-counts未指定時に使用")
    parser.add_argument("--max-numtaps", type=int, default=DEFAULT_MAX_NUMTAPS)
    parser.add_argument("--max-delay-ms", type=float, default=None, help="許容する最大FIR群遅延[ms]. 例: 10")
    parser.add_argument("--wor-n", type=int, default=DEFAULT_WOR_N)
    parser.add_argument("--plot-max-freq", type=float, default=DEFAULT_PLOT_MAX_FREQ)
    parser.add_argument("--duration-ms", type=float, default=DEFAULT_DURATION_MS)
    parser.add_argument("--settle-ms", type=float, default=DEFAULT_SETTLE_MS)
    parser.add_argument("--plot-window-ms", type=float, default=DEFAULT_PLOT_WINDOW_MS)
    parser.add_argument("--test-freqs", help="時間応答用入力周波数[Hz]. 例: 5,10,20")
    parser.add_argument("--test-amps", help="時間応答用入力振幅. 例: 0.5,1,0.5")
    parser.add_argument("--delay-ylim", help="群遅延のy軸範囲. 例: 0,1000")
    parser.add_argument("--save-json", help="候補と係数をJSON保存するパス")
    parser.add_argument("--save-figure", help="グラフ画像保存パス")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--print-coefficients", choices=("best", "all", "none"), default="best")
    parser.add_argument("--no-show", action="store_true", help="グラフを表示しない")
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.pass_low_pos is not None:
        args.pass_low = args.pass_low_pos
    if args.pass_high_pos is not None:
        args.pass_high = args.pass_high_pos
    if args.pass_low is None:
        args.pass_low = DEFAULT_PASS_LOW
    if args.pass_high is None:
        args.pass_high = DEFAULT_PASS_HIGH
    args.max_numtaps = make_odd_numtaps(args.max_numtaps)
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args = normalize_args(args)

    if argv is None:
        import sys

        if len(sys.argv) == 1:
            apply_interactive_inputs(args)

    try:
        spec = build_spec(args)
        print_spec(spec, args)
        candidates = build_candidates(args, spec)
        print_candidates(candidates, spec, args.print_coefficients)
        if args.save_json:
            save_candidates(Path(args.save_json), args, spec, candidates)
        plot_candidates(candidates, spec, args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
