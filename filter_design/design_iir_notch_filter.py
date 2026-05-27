#!/usr/bin/env python
"""
中心周波数とQを指定してIIRノッチフィルタを設計するスクリプト.

電源ノイズの50Hz/60Hzなど, 特定周波数だけを落としたい用途を想定.
IIRノッチは通常2次フィルタで, Qが大きいほど狭く深いノッチになる.

例:
  python filter_design\\design_iir_notch_filter.py --target-freq 50
  python filter_design\\design_iir_notch_filter.py 60 --q-values 20,30,50 --no-show
  python filter_design\\design_iir_notch_filter.py --target-freq 50 --bandwidth-hz 2
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


# ===== 初期値設定 =====

# サンプリング周波数[Hz].
DEFAULT_SAMPLERATE = 1000.0

# ノッチ中心周波数[Hz]. 東日本の商用電源ノイズを想定して50Hzを既定値にする.
DEFAULT_TARGET_FREQ = 50.0

# 比較するQ値. Q = target_freq / notch_bandwidth_3db の目安.
DEFAULT_Q_VALUES = "10,20,30,50,100"

# ノッチ中心で要求する最小減衰[dB].
DEFAULT_MIN_NOTCH_DEPTH_DB = 40.0

# 極の絶対値の上限. 1未満なら理論上安定.
DEFAULT_MAX_POLE_ABS = 0.99999

# 周波数応答の計算点数.
DEFAULT_WOR_N = 16000

# グラフ表示の最大周波数[Hz].
DEFAULT_PLOT_MAX_FREQ = 120.0

# 時間応答グラフ用の長さ.
DEFAULT_DURATION_MS = 3000.0
DEFAULT_SETTLE_MS = 500.0
DEFAULT_PLOT_WINDOW_MS = 500.0

# グラフ画像の保存解像度.
DEFAULT_DPI = 150


def json_number(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


@dataclass(frozen=True)
class NotchSpec:
    samplerate: float
    target_freq: float
    min_notch_depth_db: float

    @property
    def nyquist(self) -> float:
        return self.samplerate / 2.0


@dataclass(frozen=True)
class NotchCandidate:
    method: str
    q_value: float
    expected_bandwidth_hz: float
    notch_bandwidth_3db: float
    actual_q_3db: float
    order: int
    b: np.ndarray
    a: np.ndarray
    frequencies: np.ndarray
    response_db: np.ndarray
    delay_freq: np.ndarray
    delay_ms: np.ndarray
    target_gain_db: float
    target_delay_ms: float
    max_abs_delay_ms: float
    max_pole_abs: float
    stable: bool
    depth_violation_db: float
    meets_spec: bool

    def to_json_dict(self, spec: NotchSpec) -> dict:
        return {
            "method": self.method,
            "target_freq": spec.target_freq,
            "q_value": json_number(self.q_value),
            "expected_bandwidth_hz": json_number(self.expected_bandwidth_hz),
            "notch_bandwidth_3db": json_number(self.notch_bandwidth_3db),
            "actual_q_3db": json_number(self.actual_q_3db),
            "order": self.order,
            "a": [float(value) for value in self.a],
            "b": [float(value) for value in self.b],
            "target_gain_db": json_number(self.target_gain_db),
            "target_delay_ms": json_number(self.target_delay_ms),
            "max_abs_delay_ms": json_number(self.max_abs_delay_ms),
            "max_pole_abs": json_number(self.max_pole_abs),
            "stable": self.stable,
            "depth_violation_db": json_number(self.depth_violation_db),
            "meets_spec": self.meets_spec,
        }


def require_scipy_signal():
    try:
        from scipy import signal
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "scipy が見つからないためIIRノッチフィルタ設計を実行できない. "
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
    print("IIRノッチフィルタ設計, Enterで既定値を使用.")
    args.samplerate = ask_float("サンプリング周波数[Hz]", args.samplerate)
    args.target_freq = ask_float("ノッチ中心周波数[Hz]", args.target_freq or DEFAULT_TARGET_FREQ)
    args.q_values = ask_text("Q値候補. 例: 10,20,30,50,100", args.q_values)
    args.min_notch_depth_db = ask_float("ノッチ中心の最小減衰[dB]", args.min_notch_depth_db)
    show_text = ask_text("グラフを表示する [Y/n]", "Y").lower()
    if show_text in ("n", "no"):
        args.no_show = True


def build_spec(args: argparse.Namespace) -> NotchSpec:
    target_freq = args.target_freq
    if target_freq is None:
        target_freq = DEFAULT_TARGET_FREQ
    spec = NotchSpec(
        samplerate=float(args.samplerate),
        target_freq=float(target_freq),
        min_notch_depth_db=float(args.min_notch_depth_db),
    )
    validate_spec(spec)
    return spec


def validate_spec(spec: NotchSpec) -> None:
    if spec.samplerate <= 0:
        raise ValueError("samplerate は正の値")
    if spec.target_freq <= 0:
        raise ValueError("target_freq は0Hzより大きくする")
    if spec.target_freq >= spec.nyquist:
        raise ValueError(f"target_freq はナイキスト周波数 {spec.nyquist:g} Hz 未満にする")
    if spec.min_notch_depth_db <= 0:
        raise ValueError("min_notch_depth_db は正の値")


def normalize_coefficients(b: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a[0] == 1.0:
        return b, a
    return b / a[0], a / a[0]


def candidate_q_values(args: argparse.Namespace, spec: NotchSpec) -> list[float]:
    if args.bandwidth_hz is not None:
        if args.bandwidth_hz <= 0:
            raise ValueError("--bandwidth-hz は正の値")
        raw_values = [spec.target_freq / args.bandwidth_hz]
    elif args.q is not None:
        raw_values = [args.q]
    else:
        raw_values = parse_float_list(args.q_values)

    q_values = sorted({float(value) for value in raw_values})
    if not q_values:
        raise ValueError("Q値候補が空")
    if any(value <= 0 for value in q_values):
        raise ValueError("Q値は正の値")
    return q_values


def design_notch(q_value: float, spec: NotchSpec) -> tuple[np.ndarray, np.ndarray]:
    signal = require_scipy_signal()
    b, a = signal.iirnotch(w0=spec.target_freq, Q=q_value, fs=spec.samplerate)
    b = np.asarray(b, dtype=float)
    a = np.asarray(a, dtype=float)
    return normalize_coefficients(b, a)


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


def compute_group_delay(b: np.ndarray, a: np.ndarray, samplerate: float, wor_n: int):
    signal = require_scipy_signal()
    w = np.linspace(0.0, np.pi, wor_n, endpoint=False)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*denominator is extremely small.*",
            category=UserWarning,
        )
        w_delay, delay_samples = signal.group_delay((b, a), w=w)
    delay_freq = w_delay / (2.0 * np.pi) * samplerate
    delay_ms = delay_samples / samplerate * 1000.0
    return delay_freq, delay_ms


def exact_gain_db(b: np.ndarray, a: np.ndarray, samplerate: float, freq_hz: float) -> float:
    signal = require_scipy_signal()
    w = np.asarray([2.0 * np.pi * freq_hz / samplerate], dtype=float)
    _w, h = signal.freqz(b, a, worN=w)
    magnitude = max(float(abs(h[0])), 1e-20)
    return 20.0 * math.log10(magnitude)


def nearest_value(x_values: np.ndarray, y_values: np.ndarray, x: float) -> float:
    index = int(np.argmin(np.abs(x_values - x)))
    return float(y_values[index])


def compute_notch_bandwidth(frequencies: np.ndarray, response_db: np.ndarray, target_freq: float) -> float:
    if frequencies.size == 0:
        return 0.0

    target_index = int(np.argmin(np.abs(frequencies - target_freq)))
    rejected = np.isfinite(response_db) & (response_db <= -3.0)
    if target_index >= rejected.size or not rejected[target_index]:
        return 0.0

    left = target_index
    while left > 0 and rejected[left - 1]:
        left -= 1
    right = target_index
    while right < rejected.size - 1 and rejected[right + 1]:
        right += 1

    return float(frequencies[right] - frequencies[left])


def pole_frequency_hz(pole: complex, samplerate: float) -> float:
    return float(abs(np.angle(pole)) / (2.0 * np.pi) * samplerate)


def max_radius_pole_label(poles: np.ndarray, samplerate: float) -> str:
    if poles.size == 0:
        return "none"
    index = int(np.argmax(np.abs(poles)))
    pole = poles[index]
    radius = float(abs(pole))
    freq_hz = pole_frequency_hz(pole, samplerate)
    return f"|p|max={radius:.6f}, f={freq_hz:.2f} Hz"


def evaluate_candidate(q_value: float, b: np.ndarray, a: np.ndarray, spec: NotchSpec, args: argparse.Namespace) -> NotchCandidate:
    frequencies, response_db = compute_frequency_response(b, a, spec.samplerate, args.wor_n)
    delay_freq, delay_ms = compute_group_delay(b, a, spec.samplerate, args.wor_n)
    poles = np.roots(a) if len(a) > 1 else np.asarray([], dtype=complex)
    max_pole_abs = float(np.max(np.abs(poles))) if poles.size else 0.0
    stable = bool(max_pole_abs < 1.0)

    expected_bandwidth_hz = spec.target_freq / q_value
    notch_bandwidth_3db = compute_notch_bandwidth(frequencies, response_db, spec.target_freq)
    actual_q_3db = spec.target_freq / notch_bandwidth_3db if notch_bandwidth_3db > 0 else float("inf")
    target_gain_db = exact_gain_db(b, a, spec.samplerate, spec.target_freq)
    # ノッチ中心は零点そのものなので群遅延は特異点になり, 指標として使わない.
    target_delay_ms = float("nan")

    delay_mask = (
        (delay_freq <= args.plot_max_freq)
        & np.isfinite(delay_ms)
        & (np.abs(delay_freq - spec.target_freq) > max(expected_bandwidth_hz * 0.05, 1e-6))
    )
    max_abs_delay_ms = float(np.max(np.abs(delay_ms[delay_mask]))) if np.any(delay_mask) else float("nan")
    depth_violation_db = max(0.0, target_gain_db + spec.min_notch_depth_db)
    meets_spec = bool(stable and max_pole_abs <= args.max_pole_abs and depth_violation_db == 0.0)

    return NotchCandidate(
        method="iirnotch",
        q_value=q_value,
        expected_bandwidth_hz=expected_bandwidth_hz,
        notch_bandwidth_3db=notch_bandwidth_3db,
        actual_q_3db=actual_q_3db,
        order=len(a) - 1,
        b=b,
        a=a,
        frequencies=frequencies,
        response_db=response_db,
        delay_freq=delay_freq,
        delay_ms=delay_ms,
        target_gain_db=target_gain_db,
        target_delay_ms=target_delay_ms,
        max_abs_delay_ms=max_abs_delay_ms,
        max_pole_abs=max_pole_abs,
        stable=stable,
        depth_violation_db=depth_violation_db,
        meets_spec=meets_spec,
    )


def sort_candidates(candidates: list[NotchCandidate]) -> list[NotchCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            not item.meets_spec,
            item.depth_violation_db,
            item.notch_bandwidth_3db,
            abs(item.max_abs_delay_ms) if np.isfinite(item.max_abs_delay_ms) else float("inf"),
            item.max_pole_abs,
            item.q_value,
        ),
    )


def build_candidates(args: argparse.Namespace, spec: NotchSpec) -> list[NotchCandidate]:
    candidates: list[NotchCandidate] = []
    for q_value in candidate_q_values(args, spec):
        try:
            b, a = design_notch(q_value, spec)
            candidate = evaluate_candidate(q_value, b, a, spec, args)
        except Exception as exc:
            print(f"Q={q_value:g}: 設計スキップ: {exc}")
            continue

        if candidate.max_pole_abs > args.max_pole_abs:
            print(
                f"Q={q_value:g}: max_pole_abs={candidate.max_pole_abs:.8f} が "
                f"max_pole_abs={args.max_pole_abs:.8f} を超えるため除外"
            )
            continue
        candidates.append(candidate)
    return sort_candidates(candidates)


def format_tuple(values: Iterable[float], indent: str = "    ") -> str:
    lines = [f"{indent}{float(value):.18g}," for value in values]
    return "(\n" + "\n".join(lines) + "\n)"


def print_spec(spec: NotchSpec, args: argparse.Namespace) -> None:
    print("IIR notch specification:")
    print(f"  samplerate: {spec.samplerate:g} Hz")
    print(f"  target_freq: {spec.target_freq:g} Hz")
    print(f"  min_notch_depth: {spec.min_notch_depth_db:g} dB")
    print(f"  Q candidates: {', '.join(f'{value:g}' for value in candidate_q_values(args, spec))}")
    print()


def print_one_candidate(rank: int, candidate: NotchCandidate, spec: NotchSpec) -> None:
    print(f"Rank {rank}:")
    print(f"  method: {candidate.method}")
    print(f"  Q: {candidate.q_value:.6g}")
    print(f"  order: {candidate.order}")
    print(f"  expected bandwidth: {candidate.expected_bandwidth_hz:.4f} Hz")
    print(f"  notch bandwidth 3dB: {candidate.notch_bandwidth_3db:.4f} Hz")
    print(f"  actual Q 3dB: {candidate.actual_q_3db:.2f}")
    print(f"  gain_at_{spec.target_freq:g}Hz: {candidate.target_gain_db:.2f} dB")
    if np.isfinite(candidate.target_delay_ms):
        print(f"  target_delay: {candidate.target_delay_ms:.2f} ms")
    else:
        print("  target_delay: undefined at notch center")
    print(f"  max_abs_delay_in_plot: {candidate.max_abs_delay_ms:.2f} ms")
    print(f"  max_pole_abs: {candidate.max_pole_abs:.8f}")
    print(f"  stable: {'yes' if candidate.stable else 'no'}")
    print(f"  meets_spec: {'yes' if candidate.meets_spec else 'no'}")
    if not candidate.meets_spec:
        print(f"  violations: notch_depth={candidate.depth_violation_db:.2f} dB")
    print()


def print_coefficients(label: str, candidate: NotchCandidate) -> None:
    print(f"{label}_NOTCH_A = " + format_tuple(candidate.a))
    print()
    print(f"{label}_NOTCH_B = " + format_tuple(candidate.b))
    print()


def print_candidates(candidates: list[NotchCandidate], spec: NotchSpec, print_mode: str) -> None:
    if not candidates:
        print("IIRノッチフィルタ候補なし.")
        print("Q値を下げる, target_freqを見直す, max_pole_absを緩める.")
        return

    for rank, candidate in enumerate(candidates, start=1):
        print_one_candidate(rank, candidate, spec)

    if print_mode == "none":
        return
    if print_mode == "all":
        print("ランキング全ての係数 a,b:")
        for rank, candidate in enumerate(candidates, start=1):
            print(f"# Rank {rank}: method={candidate.method}, Q={candidate.q_value:g}")
            print_coefficients(f"RANK_{rank}", candidate)
        return

    print("貼り付け用係数(Rank 1):")
    print_coefficients("DEFAULT", candidates[0])


def save_candidates(path: Path, args: argparse.Namespace, spec: NotchSpec, candidates: list[NotchCandidate]) -> None:
    payload = {
        "settings": {
            "samplerate": spec.samplerate,
            "target_freq": spec.target_freq,
            "min_notch_depth_db": spec.min_notch_depth_db,
            "q_values": candidate_q_values(args, spec),
            "max_pole_abs": args.max_pole_abs,
            "filter_note": "IIR notch filter designed by scipy.signal.iirnotch. Apply with signal.lfilter(b, a, x) or signal.filtfilt(b, a, x) for offline zero-phase use.",
        },
        "candidates": [candidate.to_json_dict(spec) for candidate in candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON saved: {path}")


def build_test_signal(spec: NotchSpec, args: argparse.Namespace):
    signal = require_scipy_signal()
    test_freqs = parse_float_list(args.test_freqs) if args.test_freqs else [
        max(0.1, spec.target_freq - 5.0),
        spec.target_freq,
        spec.target_freq + 5.0,
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
    return signal, time_sec, x


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


def plot_candidates(candidates: list[NotchCandidate], spec: NotchSpec, args: argparse.Namespace) -> None:
    if not candidates:
        return
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib が見つからないためグラフ表示をスキップ.")
        return

    signal, time_sec, x = build_test_signal(spec, args)
    plot_start_sec = args.settle_ms / 1000.0
    plot_end_sec = min(args.duration_ms / 1000.0, plot_start_sec + args.plot_window_ms / 1000.0)
    time_mask = (time_sec >= plot_start_sec) & (time_sec <= plot_end_sec)

    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    ax_response = axes[0, 0]
    ax_delay = axes[1, 0]
    ax_zplane = axes[0, 1]
    ax_time = axes[1, 1]
    fig.suptitle("IIR notch filter design: response / delay / z-plane / time waveform")

    candidate_artists: dict[int, list] = {}
    labels: list[str] = []

    ax_response.axvline(spec.target_freq, color="black", linestyle="-.", linewidth=1.0, label="target")
    ax_response.axhline(-spec.min_notch_depth_db, color="red", linestyle="--", linewidth=1.0, label=f"-depth ({-spec.min_notch_depth_db:g} dB)")
    ax_delay.axvline(spec.target_freq, color="black", linestyle="-.", linewidth=1.0)

    theta = np.linspace(0.0, 2.0 * np.pi, 720)
    ax_zplane.plot(np.cos(theta), np.sin(theta), linestyle="--", linewidth=1.0, color="0.4", label="Unit circle")
    ax_zplane.axhline(0.0, linewidth=0.8, color="0.5")
    ax_zplane.axvline(0.0, linewidth=0.8, color="0.5")
    ax_time.plot(time_sec[time_mask] * 1000.0, x[time_mask], color="0.25", linewidth=1.2, label="Input")

    for rank, candidate in enumerate(candidates, start=1):
        label = f"R{rank} Q{candidate.q_value:g}"
        labels.append(label)
        artists = []

        half_width = candidate.notch_bandwidth_3db / 2.0 if candidate.notch_bandwidth_3db > 0 else candidate.expected_bandwidth_hz / 2.0
        span = ax_response.axvspan(
            spec.target_freq - half_width,
            spec.target_freq + half_width,
            alpha=0.08,
            label=f"{label} 3dB width",
        )
        artists.append(span)

        freq_mask = candidate.frequencies <= args.plot_max_freq
        line_response, = ax_response.plot(
            candidate.frequencies[freq_mask],
            candidate.response_db[freq_mask],
            linewidth=1.4,
            label=(
                f"{label}, width={candidate.notch_bandwidth_3db:.2f} Hz, "
                f"gain={candidate.target_gain_db:.1f} dB"
            ),
        )
        artists.append(line_response)

        delay_mask = (
            (candidate.delay_freq <= args.plot_max_freq)
            & np.isfinite(candidate.delay_ms)
        )
        line_delay, = ax_delay.plot(
            candidate.delay_freq[delay_mask],
            candidate.delay_ms[delay_mask],
            linewidth=1.4,
            label=f"{label}, max={candidate.max_abs_delay_ms:.1f} ms",
        )
        artists.append(line_delay)

        zeros = np.roots(candidate.b) if len(candidate.b) > 1 else np.asarray([], dtype=complex)
        poles = np.roots(candidate.a) if len(candidate.a) > 1 else np.asarray([], dtype=complex)
        if zeros.size:
            zero_artist = ax_zplane.scatter(
                np.real(zeros),
                np.imag(zeros),
                marker="o",
                facecolors="none",
                s=34,
                label=f"{label} zeros",
            )
            artists.append(zero_artist)
        if poles.size:
            pole_text = max_radius_pole_label(poles, spec.samplerate)
            stable_text = "stable" if candidate.stable else "unstable"
            pole_artist = ax_zplane.scatter(
                np.real(poles),
                np.imag(poles),
                marker="x",
                s=34,
                label=f"{label} poles, {pole_text}, {stable_text}",
            )
            artists.append(pole_artist)

        y = signal.lfilter(candidate.b, candidate.a, x)
        line_time, = ax_time.plot(
            time_sec[time_mask] * 1000.0,
            y[time_mask],
            linewidth=1.2,
            label=label,
        )
        artists.append(line_time)
        candidate_artists[rank - 1] = artists

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

    ax_delay.set_title("Group delay")
    ax_delay.set_xlabel("Frequency [Hz]")
    ax_delay.set_ylabel("Delay [ms]")
    ax_delay.set_xlim(0.0, args.plot_max_freq)
    if args.delay_ylim:
        ymin, ymax = parse_pair(args.delay_ylim, "--delay-ylim")
        ax_delay.set_ylim(ymin, ymax)
    ax_delay.grid(True)
    ax_delay.legend(loc="best", fontsize=8)

    ax_zplane.set_title("Pole-zero plot")
    ax_zplane.set_xlabel("Real")
    ax_zplane.set_ylabel("Imaginary")
    ax_zplane.set_xlim(-1.1, 1.1)
    ax_zplane.set_ylim(-1.1, 1.1)
    ax_zplane.set_aspect("equal", adjustable="box")
    ax_zplane.grid(True)
    ax_zplane.legend(loc="best", fontsize=7)

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


def parse_pair(text: str, label: str) -> tuple[float, float]:
    values = parse_float_list(text)
    if len(values) != 2:
        raise argparse.ArgumentTypeError(f"{label} は 'low,high' の2値で指定")
    return values[0], values[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="中心周波数とQを指定してIIRノッチフィルタを設計する."
    )
    parser.add_argument("target_freq_pos", nargs="?", type=float, help="省略指定: ノッチ中心周波数[Hz]")
    parser.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--target-freq", type=float, default=None, help="ノッチ中心周波数[Hz]. 例: 50 or 60")
    parser.add_argument("--q", type=float, default=None, help="単一のQ値")
    parser.add_argument("--q-values", default=DEFAULT_Q_VALUES, help="比較するQ値. 例: 10,20,30,50,100")
    parser.add_argument("--bandwidth-hz", type=float, default=None, help="3dBノッチ幅からQを決める. Q=target_freq/bandwidth")
    parser.add_argument("--min-notch-depth-db", type=float, default=DEFAULT_MIN_NOTCH_DEPTH_DB)
    parser.add_argument("--max-pole-abs", type=float, default=DEFAULT_MAX_POLE_ABS)
    parser.add_argument("--wor-n", type=int, default=DEFAULT_WOR_N)
    parser.add_argument("--plot-max-freq", type=float, default=DEFAULT_PLOT_MAX_FREQ)
    parser.add_argument("--duration-ms", type=float, default=DEFAULT_DURATION_MS)
    parser.add_argument("--settle-ms", type=float, default=DEFAULT_SETTLE_MS)
    parser.add_argument("--plot-window-ms", type=float, default=DEFAULT_PLOT_WINDOW_MS)
    parser.add_argument("--test-freqs", help="時間応答用入力周波数[Hz]. 例: 45,50,55")
    parser.add_argument("--test-amps", help="時間応答用入力振幅. 例: 0.5,1,0.5")
    parser.add_argument("--delay-ylim", help="群遅延のy軸範囲. 例: -50,200")
    parser.add_argument("--save-json", help="候補と係数をJSON保存するパス")
    parser.add_argument("--save-figure", help="グラフ画像保存パス")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--print-coefficients", choices=("best", "all", "none"), default="all")
    parser.add_argument("--no-show", action="store_true", help="グラフを表示しない")
    return parser


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.target_freq_pos is not None:
        args.target_freq = args.target_freq_pos
    if args.target_freq is None:
        args.target_freq = DEFAULT_TARGET_FREQ
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
