#!/usr/bin/env python
"""
通過域と遷移域を直接指定してIIRバンドパスフィルタを設計するスクリプト.

design_peak_filter.py は target 周波数を中心に探索する用途.
このファイルは passband と transition width を明示して, BPF仕様どおりに設計する用途.

例:
  python filter_design\\design_bandpass_filter.py --passband 9.5,10.5 --transition 1.0
  python filter_design\\design_bandpass_filter.py --pass-low 9.5 --pass-high 10.5 --transition-low 1 --transition-high 1
  python filter_design\\design_bandpass_filter.py 9.5 10.5 --transition 0.5 --families butter,ellip
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
# ここを書き換えると, コマンドラインで指定しない場合の値を変更可能.

# サンプリング周波数[Hz].
DEFAULT_SAMPLERATE = 1000.0

# 通過域の初期値[Hz].
DEFAULT_PASS_LOW = 9.5
DEFAULT_PASS_HIGH = 10.5

# 遷移域の初期値[Hz]. 阻止域は pass_low-transition_low, pass_high+transition_high.
# 直接形a,bで安全に動かしやすい値. より鋭くする場合は --transition を小さくする.
DEFAULT_TRANSITION_LOW = 3.0
DEFAULT_TRANSITION_HIGH = 3.0

# IIR設計法. all は butter, cheby1, cheby2, ellip をすべて比較.
DEFAULT_FAMILIES = "all"

# 通過域端最大損失[dB]. 小さいほど通過域が平坦.
DEFAULT_GPASS = 1.0

# 阻止域端最小減衰[dB]. 大きいほど強く止めるが次数と遅延が増えやすい.
DEFAULT_GSTOP = 40.0

# 仕様判定の許容誤差[dB]. 直接形a,bの数値誤差を少し許容.
DEFAULT_SPEC_TOLERANCE_DB = 0.5

# 極の絶対値の上限. 1未満なら理論上安定, 1に近すぎる候補を避ける.
DEFAULT_MAX_POLE_ABS = 0.99999

# a,b直接形で許容する最大次数. 高すぎるIIRは数値誤差が出やすい.
DEFAULT_MAX_DIRECT_FORM_ORDER = 20

# 周波数応答の計算点数.
DEFAULT_WOR_N = 16000

# グラフ表示の最大周波数[Hz].
DEFAULT_PLOT_MAX_FREQ = 60.0

# 時間応答グラフ用の長さ.
DEFAULT_DURATION_MS = 3000.0
DEFAULT_SETTLE_MS = 500.0
DEFAULT_PLOT_WINDOW_MS = 500.0

# グラフ画像の保存解像度.
DEFAULT_DPI = 150


SUPPORTED_FAMILIES = ("butter", "cheby1", "cheby2", "ellip")


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


@dataclass(frozen=True)
class FilterCandidate:
    family: str
    order: int
    direct_form_order: int
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
    max_pole_abs: float
    stable: bool

    def to_json_dict(self, spec: BandpassSpec) -> dict:
        return {
            "family": self.family,
            "prototype_order": self.order,
            "direct_form_order": self.direct_form_order,
            "b": [float(value) for value in self.b],
            "a": [float(value) for value in self.a],
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
            "max_pole_abs": self.max_pole_abs,
            "stable": self.stable,
        }


def require_scipy_signal():
    try:
        from scipy import signal
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "scipy が見つからないためフィルタ設計を実行できない. "
            "conda env update -f environment.yml --prune を実行."
        ) from exc
    return signal


def parse_float_list(text: str) -> list[float]:
    if text is None:
        return []
    values: list[float] = []
    for raw in text.replace(";", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        values.append(float(raw))
    return values


def parse_pair(text: str, label: str) -> tuple[float, float]:
    values = parse_float_list(text)
    if len(values) != 2:
        raise argparse.ArgumentTypeError(f"{label} は 'low,high' の2値で指定")
    return values[0], values[1]


def parse_families(text: str) -> list[str]:
    text = (text or "").strip().lower()
    if text in ("", "all"):
        return list(SUPPORTED_FAMILIES)

    families = [item.strip().lower() for item in text.replace(";", ",").split(",") if item.strip()]
    unknown = [family for family in families if family not in SUPPORTED_FAMILIES]
    if unknown:
        raise argparse.ArgumentTypeError(f"未対応のfamily: {', '.join(unknown)}")
    return families


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
    print("BPF設計, Enterで既定値を使用.")
    args.samplerate = ask_float("サンプリング周波数[Hz]", args.samplerate)
    args.pass_low = ask_float("通過域下限[Hz]", args.pass_low or DEFAULT_PASS_LOW)
    args.pass_high = ask_float("通過域上限[Hz]", args.pass_high or DEFAULT_PASS_HIGH)
    args.transition_low = ask_float("低周波側の遷移域幅[Hz]", args.transition_low)
    args.transition_high = ask_float("高周波側の遷移域幅[Hz]", args.transition_high)
    args.families = ask_text("設計法 all/butter/cheby1/cheby2/ellip", args.families)
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

    stop_low = pass_low - transition_low
    stop_high = pass_high + transition_high
    spec = BandpassSpec(
        samplerate=float(args.samplerate),
        pass_low=float(pass_low),
        pass_high=float(pass_high),
        transition_low=float(transition_low),
        transition_high=float(transition_high),
        stop_low=float(stop_low),
        stop_high=float(stop_high),
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
    if spec.stop_low >= spec.pass_low:
        raise ValueError("stop_low は pass_low より小さくする")
    if spec.pass_high >= spec.stop_high:
        raise ValueError("pass_high は stop_high より小さくする")
    if spec.stop_high >= nyquist:
        raise ValueError(f"stop_high はナイキスト周波数 {nyquist:g} Hz 未満にする")
    if spec.gpass <= 0 or spec.gstop <= 0:
        raise ValueError("gpass, gstop は正の値")
    if spec.gstop <= spec.gpass:
        raise ValueError("gstop は gpass より大きくする")


def normalize_coefficients(b: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a[0] == 1.0:
        return b, a
    return b / a[0], a / a[0]


def design_family(family: str, spec: BandpassSpec) -> tuple[int, np.ndarray, np.ndarray]:
    signal = require_scipy_signal()
    nyquist = spec.samplerate / 2.0
    wp = np.asarray([spec.pass_low / nyquist, spec.pass_high / nyquist], dtype=float)
    ws = np.asarray([spec.stop_low / nyquist, spec.stop_high / nyquist], dtype=float)

    if family == "butter":
        order, wn = signal.buttord(wp, ws, spec.gpass, spec.gstop)
        b, a = signal.butter(order, wn, btype="bandpass")
    elif family == "cheby1":
        order, wn = signal.cheb1ord(wp, ws, spec.gpass, spec.gstop)
        b, a = signal.cheby1(order, spec.gpass, wn, btype="bandpass")
    elif family == "cheby2":
        order, wn = signal.cheb2ord(wp, ws, spec.gpass, spec.gstop)
        b, a = signal.cheby2(order, spec.gstop, wn, btype="bandpass")
    elif family == "ellip":
        order, wn = signal.ellipord(wp, ws, spec.gpass, spec.gstop)
        b, a = signal.ellip(order, spec.gpass, spec.gstop, wn, btype="bandpass")
    else:
        raise ValueError(f"未対応family: {family}")

    b = np.asarray(b, dtype=float)
    a = np.asarray(a, dtype=float)
    b, a = normalize_coefficients(b, a)
    return int(order), b, a


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


def evaluate_candidate(family: str, order: int, b: np.ndarray, a: np.ndarray, spec: BandpassSpec, wor_n: int) -> FilterCandidate:
    frequencies, response_db = compute_frequency_response(b, a, spec.samplerate, wor_n)
    delay_freq, delay_ms = compute_group_delay(b, a, spec.samplerate, wor_n)
    poles = np.roots(a) if len(a) > 1 else np.asarray([], dtype=complex)
    max_pole_abs = float(np.max(np.abs(poles))) if poles.size else 0.0
    stable = bool(max_pole_abs < 1.0)

    pass_mask = (frequencies >= spec.pass_low) & (frequencies <= spec.pass_high)
    stop_mask = (frequencies <= spec.stop_low) | (frequencies >= spec.stop_high)
    stop_mask &= frequencies <= (spec.samplerate / 2.0)

    passband_min_db = float(np.min(response_db[pass_mask])) if np.any(pass_mask) else float("nan")
    passband_max_db = float(np.max(response_db[pass_mask])) if np.any(pass_mask) else float("nan")
    stopband_max_db = float(np.max(response_db[stop_mask])) if np.any(stop_mask) else float("nan")
    center_gain_db = nearest_value(frequencies, response_db, spec.center_freq)
    center_delay_ms = nearest_value(delay_freq, delay_ms, spec.center_freq)
    bandwidth_3db = compute_3db_bandwidth(frequencies, response_db, spec.center_freq)
    q_value = spec.center_freq / bandwidth_3db if bandwidth_3db > 0 else float("inf")

    return FilterCandidate(
        family=family,
        order=order,
        direct_form_order=len(a) - 1,
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
        max_pole_abs=max_pole_abs,
        stable=stable,
    )


def sort_candidates(candidates: list[FilterCandidate]) -> list[FilterCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            not item.stable,
            -item.q_value if np.isfinite(item.q_value) else float("-inf"),
            item.stopband_max_db,
            item.direct_form_order,
            abs(item.center_delay_ms) if np.isfinite(item.center_delay_ms) else float("inf"),
            item.max_pole_abs,
        ),
    )


def build_candidates(args: argparse.Namespace, spec: BandpassSpec) -> list[FilterCandidate]:
    families = parse_families(args.families)
    candidates: list[FilterCandidate] = []
    for family in families:
        try:
            order, b, a = design_family(family, spec)
            candidate = evaluate_candidate(family, order, b, a, spec, args.wor_n)
        except Exception as exc:
            print(f"{family}: 設計スキップ: {exc}")
            continue

        if candidate.direct_form_order > args.max_direct_form_order:
            print(
                f"{family}: direct_form_order={candidate.direct_form_order} が "
                f"max_direct_form_order={args.max_direct_form_order} を超えるため除外"
            )
            continue
        if candidate.max_pole_abs > args.max_pole_abs:
            print(
                f"{family}: max_pole_abs={candidate.max_pole_abs:.8f} が "
                f"max_pole_abs={args.max_pole_abs:.8f} を超えるため除外"
            )
            continue
        if candidate.passband_min_db < -spec.gpass - args.spec_tolerance_db:
            print(
                f"{family}: passband_min={candidate.passband_min_db:.2f} dB が "
                f"許容値 {-spec.gpass - args.spec_tolerance_db:.2f} dB 未満のため除外"
            )
            continue
        if candidate.stopband_max_db > -spec.gstop + args.spec_tolerance_db:
            print(
                f"{family}: stopband_max={candidate.stopband_max_db:.2f} dB が "
                f"許容値 {-spec.gstop + args.spec_tolerance_db:.2f} dB を超えるため除外"
            )
            continue
        candidates.append(candidate)
    return sort_candidates(candidates)


def format_tuple(values: Iterable[float], indent: str = "    ") -> str:
    lines = [f"{indent}{float(value):.18g}," for value in values]
    return "(\n" + "\n".join(lines) + "\n)"


def print_spec(spec: BandpassSpec) -> None:
    print("BPF specification:")
    print(f"  samplerate: {spec.samplerate:g} Hz")
    print(f"  passband: {spec.pass_low:g} Hz - {spec.pass_high:g} Hz")
    print(f"  transition low/high: {spec.transition_low:g} Hz / {spec.transition_high:g} Hz")
    print(f"  stopband: <= {spec.stop_low:g} Hz, >= {spec.stop_high:g} Hz")
    print(f"  gpass: {spec.gpass:g} dB")
    print(f"  gstop: {spec.gstop:g} dB")
    print()


def print_candidates(candidates: list[FilterCandidate], spec: BandpassSpec) -> None:
    if not candidates:
        print("条件に合うフィルタ候補なし.")
        print("transitionを広げる, gstopを下げる, max_direct_form_orderやmax_pole_absを緩める.")
        return

    for rank, candidate in enumerate(candidates, start=1):
        print(f"Rank {rank}:")
        print(f"  family: {candidate.family}")
        print(f"  prototype_order: {candidate.order}")
        print(f"  direct_form_order: {candidate.direct_form_order}")
        print(f"  passband gain min/max: {candidate.passband_min_db:.2f} / {candidate.passband_max_db:.2f} dB")
        print(f"  stopband max gain: {candidate.stopband_max_db:.2f} dB")
        print(f"  gain_at_{spec.center_freq:g}Hz: {candidate.center_gain_db:.2f} dB")
        print(f"  center_delay: {candidate.center_delay_ms:.2f} ms")
        print(f"  bandwidth_3db: {candidate.bandwidth_3db:.4f} Hz")
        print(f"  Q: {candidate.q_value:.2f}")
        print(f"  max_pole_abs: {candidate.max_pole_abs:.8f}")
        print(f"  stable: {'yes' if candidate.stable else 'no'}")
        print()

    print("ランキング全ての係数 a,b:")
    for rank, candidate in enumerate(candidates, start=1):
        print(
            f"# Rank {rank}: family={candidate.family}, "
            f"prototype_order={candidate.order}, "
            f"direct_form_order={candidate.direct_form_order}"
        )
        print(f"RANK_{rank}_BANDPASS_A = " + format_tuple(candidate.a))
        print()
        print(f"RANK_{rank}_BANDPASS_B = " + format_tuple(candidate.b))
        print()

    print("PhaseTiming.py へ貼る係数(Rank 1):")
    print("DEFAULT_BANDPASS_A = " + format_tuple(candidates[0].a))
    print()
    print("DEFAULT_BANDPASS_B = " + format_tuple(candidates[0].b))


def save_candidates(path: Path, args: argparse.Namespace, spec: BandpassSpec, candidates: list[FilterCandidate]) -> None:
    payload = {
        "settings": {
            "samplerate": spec.samplerate,
            "passband": [spec.pass_low, spec.pass_high],
            "transition": [spec.transition_low, spec.transition_high],
            "stopband": [spec.stop_low, spec.stop_high],
            "gpass": spec.gpass,
            "gstop": spec.gstop,
            "families": parse_families(args.families),
            "max_pole_abs": args.max_pole_abs,
            "max_direct_form_order": args.max_direct_form_order,
            "spec_tolerance_db": args.spec_tolerance_db,
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


def plot_candidates(candidates: list[FilterCandidate], spec: BandpassSpec, args: argparse.Namespace) -> None:
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
    ax_zplane = axes[0, 1]
    ax_time = axes[1, 1]
    fig.suptitle("Bandpass filter design: response / delay / z-plane / time waveform")

    candidate_artists: dict[int, list] = {}
    labels: list[str] = []

    shade_filter_regions(ax_response, spec)
    shade_filter_regions(ax_delay, spec)
    ax_response.axhline(-spec.gpass, color="green", linestyle="--", linewidth=1.0, label=f"-gpass ({-spec.gpass:g} dB)")
    ax_response.axhline(-spec.gstop, color="red", linestyle="--", linewidth=1.0, label=f"-gstop ({-spec.gstop:g} dB)")

    theta = np.linspace(0.0, 2.0 * np.pi, 720)
    ax_zplane.plot(np.cos(theta), np.sin(theta), linestyle="--", linewidth=1.0, color="0.4", label="Unit circle")
    ax_zplane.axhline(0.0, linewidth=0.8, color="0.5")
    ax_zplane.axvline(0.0, linewidth=0.8, color="0.5")
    ax_time.plot(time_sec[time_mask] * 1000.0, x[time_mask], color="0.25", linewidth=1.2, label="Input")

    for rank, candidate in enumerate(candidates, start=1):
        label = f"R{rank} {candidate.family} o{candidate.direct_form_order}"
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

        delay_mask = (
            (candidate.delay_freq <= args.plot_max_freq)
            & np.isfinite(candidate.delay_ms)
        )
        line_delay, = ax_delay.plot(
            candidate.delay_freq[delay_mask],
            candidate.delay_ms[delay_mask],
            linewidth=1.4,
            label=f"{label}, center={candidate.center_delay_ms:.1f} ms",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通過域と遷移域を直接指定してIIRバンドパスフィルタを設計する."
    )
    parser.add_argument("pass_low_pos", nargs="?", type=float, help="省略指定: 通過域下限[Hz]")
    parser.add_argument("pass_high_pos", nargs="?", type=float, help="省略指定: 通過域上限[Hz]")
    parser.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--passband", help="通過域. 例: 9.5,10.5")
    parser.add_argument("--pass-low", dest="pass_low", type=float, default=None)
    parser.add_argument("--pass-high", dest="pass_high", type=float, default=None)
    parser.add_argument("--transition", help="遷移域幅. 例: 1.0 または 0.8,1.2")
    parser.add_argument("--transition-low", type=float, default=DEFAULT_TRANSITION_LOW)
    parser.add_argument("--transition-high", type=float, default=DEFAULT_TRANSITION_HIGH)
    parser.add_argument("--families", default=DEFAULT_FAMILIES, help="all, butter, cheby1, cheby2, ellip, またはカンマ区切り")
    parser.add_argument("--gpass", type=float, default=DEFAULT_GPASS)
    parser.add_argument("--gstop", type=float, default=DEFAULT_GSTOP)
    parser.add_argument("--spec-tolerance-db", type=float, default=DEFAULT_SPEC_TOLERANCE_DB)
    parser.add_argument("--max-pole-abs", type=float, default=DEFAULT_MAX_POLE_ABS)
    parser.add_argument("--max-direct-form-order", type=int, default=DEFAULT_MAX_DIRECT_FORM_ORDER)
    parser.add_argument("--wor-n", type=int, default=DEFAULT_WOR_N)
    parser.add_argument("--plot-max-freq", type=float, default=DEFAULT_PLOT_MAX_FREQ)
    parser.add_argument("--duration-ms", type=float, default=DEFAULT_DURATION_MS)
    parser.add_argument("--settle-ms", type=float, default=DEFAULT_SETTLE_MS)
    parser.add_argument("--plot-window-ms", type=float, default=DEFAULT_PLOT_WINDOW_MS)
    parser.add_argument("--test-freqs", help="時間応答用入力周波数[Hz]. 例: 5,10,20")
    parser.add_argument("--test-amps", help="時間応答用入力振幅. 例: 0.5,1,0.5")
    parser.add_argument("--delay-ylim", help="群遅延のy軸範囲. 例: -50,200")
    parser.add_argument("--save-json", help="候補と係数をJSON保存するパス")
    parser.add_argument("--save-figure", help="グラフ画像保存パス")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
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
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args = normalize_args(args)

    # 引数なし実行時は対話入力.
    if argv is None:
        import sys

        if len(sys.argv) == 1:
            apply_interactive_inputs(args)

    try:
        spec = build_spec(args)
        print_spec(spec)
        candidates = build_candidates(args, spec)
        print_candidates(candidates, spec)
        if args.save_json:
            save_candidates(Path(args.save_json), args, spec, candidates)
        plot_candidates(candidates, spec, args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
