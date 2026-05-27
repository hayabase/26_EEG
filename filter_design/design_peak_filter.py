#!/usr/bin/env python
"""
狙った刺激周波数を強調するピークフィルタを探索, 設計するスクリプト.

例:
    python filter_design/design_peak_filter.py 10
    python filter_design/design_peak_filter.py --target-freq 10 --samplerate 1000
    python filter_design/design_peak_filter.py 10 --max-target-delay-ms 1
    python filter_design/design_peak_filter.py --target-freq 7.5 --output filter_design/filter_7_5hz.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


SUPPORTED_FAMILIES = ("butter", "cheby1", "cheby2", "ellip")

SEARCH_PRESET_OVERRIDES = {
    "sharp": {
        "families": "butter,cheby2,ellip,cheby1",
        "passband_offset_values": "0.02,0.03,0.05,0.075,0.1,0.15,0.2,0.25,0.3,0.4,0.5,0.65,0.8,1.0",
        "stopband_gap_values": "0.05,0.1,0.2,0.35,0.5,0.75,1.0,1.5,2.5,4.0",
        "gpass_values": "0.5,1,2",
        "gstop_values": "20,40,60,80,100",
        "top_n": 20,
    },
    "exhaustive": {
        "families": "butter,cheby2,ellip,cheby1",
        "passband_offset_values": (
            "0.01,0.015,0.02,0.03,0.04,0.05,0.075,0.1,0.15,0.2,"
            "0.25,0.3,0.4,0.5,0.65,0.8,1.0,1.25,1.5"
        ),
        "stopband_gap_values": "0.025,0.05,0.075,0.1,0.15,0.2,0.35,0.5,0.75,1.0,1.5,2.5,4.0",
        "gpass_values": "0.1,0.3,0.5,1,2,3",
        "gstop_values": "20,40,60,80,100,150,200",
        "top_n": 30,
    },
}


# サンプリング周波数, EEGの取得レート.
DEFAULT_SAMPLERATE = 1000.0

# 強調したい刺激周波数, SSVEPの基本周波数.
DEFAULT_TARGET_FREQ = 10.0

# 通過帯域候補の探索幅, target_freq付近の鋭い候補を探す.
DEFAULT_PASSBAND_SEARCH_WIDTH = 1.5

# 通過帯域端候補の刻み幅, 鋭い候補を逃さない細かさ.
DEFAULT_PASSBAND_EDGE_STEP = 0.05

# 阻止帯と通過帯の間隔の最小値, 鋭い遷移域も候補に入れる.
DEFAULT_STOPBAND_GAP_MIN = 0.5

# 阻止帯と通過帯の間隔の最大値, 広めの候補も比較する.
DEFAULT_STOPBAND_GAP_MAX = 4.0

# 阻止帯と通過帯の間隔の刻み幅, 鋭さの違いを比較する.
DEFAULT_STOPBAND_GAP_STEP = 0.25

# 総当たり探索プリセット. standardは従来相当, sharp/exhaustiveは探索変数を増やす.
DEFAULT_SEARCH_PRESET = "standard"

# 複数family探索の既定値. 未指定時は --family と同じ単一familyを使う.
DEFAULT_FAMILIES = None

# targetから通過帯域端までの距離候補[Hz]. 指定時は左右オフセット総当たり.
DEFAULT_PASSBAND_OFFSET_VALUES = None

# 通過帯域候補の探索幅を複数振る場合の候補[Hz].
DEFAULT_PASSBAND_SEARCH_WIDTH_VALUES = None

# 通過帯域端刻みを複数振る場合の候補[Hz].
DEFAULT_PASSBAND_EDGE_STEP_VALUES = None

# stopband gapを明示的に総当たりする候補[Hz].
DEFAULT_STOPBAND_GAP_VALUES = None

# 通過域端最大損失[dB], 小さいほど通過帯が平坦.
DEFAULT_GPASS_VALUES = "1"

# 阻止域端最小減衰[dB], 鋭さを重視しつつ過大次数を避ける.
DEFAULT_GSTOP_VALUES = "20,60,80,100,150,200"

# target_freqで許容する最小ゲイン[dB], 刺激周波数を十分に通す.
DEFAULT_ACCEPTABLE_GAIN_DB = -1.0

# target周波数で許容する最大ゲイン[dB], +3dBを超える候補を除外.
DEFAULT_MAX_TARGET_GAIN_DB = 3.0

# target周波数周辺で発振的な盛り上がりを監視する半幅[Hz].
DEFAULT_TARGET_NEIGHBORHOOD_WIDTH = 0.5

# target周波数周辺で許容する最大ゲイン[dB], target一点だけでなく周辺も除外.
DEFAULT_MAX_NEAR_TARGET_GAIN_DB = DEFAULT_MAX_TARGET_GAIN_DB

# Q値の上限, 鋭さを残しつつ極端な遅延を避ける.
DEFAULT_MAX_Q = 150.0

# target_freqで許容する最大群遅延[ms], 大きすぎる位相ずれを避ける.
DEFAULT_MAX_TARGET_DELAY_MS = 1000.0

# 群遅延の上限を確認するtarget周波数周辺の半幅[Hz].
DEFAULT_TARGET_DELAY_NEIGHBORHOOD_WIDTH = 1.0

# target周波数周辺の群遅延を走査する点数.
DEFAULT_TARGET_DELAY_NEIGHBORHOOD_POINTS = 401

# 極の絶対値の上限, 鋭いが単位円に近すぎる候補は避ける.
DEFAULT_MAX_POLE_ABS = 0.9998

# a,b直接形で許容する最大次数, 高すぎるIIRは数値誤差が出やすい.
DEFAULT_MAX_DIRECT_FORM_ORDER = 10

# 表示, 保存する候補数.
DEFAULT_TOP_N = 10

# 周波数応答の計算点数.
DEFAULT_WOR_N = 16000

# グラフで表示する最大周波数.
DEFAULT_PLOT_MAX_FREQ = 60.0

# フィルタ探索後に最良候補を自動で検証する.
DEFAULT_AUTO_CHECK_FILTER = True

# 自動チェックするRank. allなら表示候補すべて.
DEFAULT_CHECK_RANKS = "all"

# 固定グラフに重ねる詳細表示のRank. allなら表示候補すべて.
DEFAULT_DETAIL_RANKS = "all"


SCIPY_INSTALL_MESSAGE = (
    "scipy が見つからないためフィルタ設計を実行できない.\n"
    "以下のどちらかを実行:\n"
    "  conda install -n eeg-max2 -c conda-forge scipy\n"
    "  conda env update -f environment.yml"
)


@dataclass
class FilterCandidate:
    family: str
    order: int
    b: np.ndarray
    a: np.ndarray
    fp: tuple[float, float]
    fs: tuple[float, float]
    gpass: float
    gstop: float
    acceptable_gain_db: float
    q_value: float
    bandwidth_3db: float
    gain_at_target_db: float
    near_target_max_gain_db: float
    near_target_peak_freq_hz: float
    target_delay_ms: float
    near_target_max_delay_ms: float
    near_target_delay_peak_freq_hz: float
    max_pole_abs: float
    frequencies: np.ndarray
    response_db: np.ndarray

    @property
    def direct_form_order(self) -> int:
        return len(self.a) - 1

    def to_json_dict(self) -> dict:
        return {
            "family": self.family,
            "prototype_order": self.order,
            "direct_form_order": self.direct_form_order,
            "a": self.a.tolist(),
            "b": self.b.tolist(),
            "fp": list(self.fp),
            "fs": list(self.fs),
            "gpass": self.gpass,
            "gstop": self.gstop,
            "acceptable_gain_db": self.acceptable_gain_db,
            "q_value": self.q_value,
            "bandwidth_3db": self.bandwidth_3db,
            "gain_at_target_db": self.gain_at_target_db,
            "near_target_max_gain_db": self.near_target_max_gain_db,
            "near_target_peak_freq_hz": self.near_target_peak_freq_hz,
            "target_delay_ms": self.target_delay_ms,
            "near_target_max_delay_ms": self.near_target_max_delay_ms,
            "near_target_delay_peak_freq_hz": self.near_target_delay_peak_freq_hz,
            "max_pole_abs": self.max_pole_abs,
        }


def parse_float_list(text: str) -> list[float]:
    values = []
    for item in text.split(","):
        item = item.strip()
        if item:
            values.append(float(item))
    if not values:
        raise argparse.ArgumentTypeError("値を1つ以上指定する必要あり")
    return values


def parse_optional_float_list(text: str | None) -> list[float] | None:
    if text is None:
        return None
    return parse_float_list(text)


def parse_name_list(text: str | None) -> list[str]:
    if text is None:
        return []
    values = []
    for item in text.split(","):
        item = item.strip()
        if item:
            values.append(item)
    return values


def unique_floats(values: Iterable[float], digits: int = 10) -> list[float]:
    unique = {}
    for value in values:
        rounded = round(float(value), digits)
        unique[rounded] = float(value)
    return [unique[key] for key in sorted(unique)]


def unique_pairs(values: Iterable[tuple[float, float]], digits: int = 10) -> list[tuple[float, float]]:
    unique = {}
    for left, right in values:
        key = (round(float(left), digits), round(float(right), digits))
        unique[key] = (float(left), float(right))
    return [unique[key] for key in sorted(unique)]


def frange(start: float, stop: float, step: float) -> np.ndarray:
    if step <= 0:
        raise ValueError("step must be positive")
    count = int(math.floor((stop - start) / step)) + 1
    values = start + np.arange(max(count, 0)) * step
    return values[values <= stop + step * 0.25]


def require_scipy_signal():
    try:
        import scipy.signal as signal
    except ModuleNotFoundError as exc:
        raise RuntimeError(SCIPY_INSTALL_MESSAGE) from exc
    return signal


def design_iir(signal, family: str, wp: np.ndarray, ws: np.ndarray, gpass: float, gstop: float):
    if family == "butter":
        order, wn = signal.buttord(wp, ws, gpass, gstop)
        b, a = signal.butter(order, wn, btype="band")
    elif family == "cheby1":
        order, wn = signal.cheb1ord(wp, ws, gpass, gstop)
        b, a = signal.cheby1(order, gpass, wn, btype="band")
    elif family == "cheby2":
        order, wn = signal.cheb2ord(wp, ws, gpass, gstop)
        b, a = signal.cheby2(order, gstop, wn, btype="band")
    elif family == "ellip":
        order, wn = signal.ellipord(wp, ws, gpass, gstop)
        b, a = signal.ellip(order, gpass, gstop, wn, btype="band")
    else:
        raise ValueError(f"未対応のfamily: {family}")
    return order, np.asarray(b, dtype=float), np.asarray(a, dtype=float)


def response_db(signal, b: np.ndarray, a: np.ndarray, samplerate: float, wor_n: int):
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        w, h = signal.freqz(b, a, worN=wor_n)
    frequencies = w / (2.0 * np.pi) * samplerate
    magnitude = np.abs(h)
    magnitude[magnitude == 0] = 1e-20
    return frequencies, 20.0 * np.log10(magnitude)


def target_group_delay_ms(signal, b: np.ndarray, a: np.ndarray, samplerate: float, target_freq: float):
    target_w = 2.0 * np.pi * target_freq / samplerate
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*denominator is extremely small.*",
            category=UserWarning,
        )
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        _, delay_samples = signal.group_delay((b, a), w=np.asarray([target_w]))
    delay_ms = float(delay_samples[0] / samplerate * 1000.0)
    return delay_ms


def group_delay_ms_at_frequencies(
    signal,
    b: np.ndarray,
    a: np.ndarray,
    samplerate: float,
    frequencies: np.ndarray,
) -> np.ndarray:
    w = 2.0 * np.pi * np.asarray(frequencies, dtype=float) / samplerate
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*denominator is extremely small.*",
            category=UserWarning,
        )
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        _, delay_samples = signal.group_delay((b, a), w=w)
    return np.asarray(delay_samples, dtype=float) / samplerate * 1000.0


def target_and_near_delay_ms(
    signal,
    b: np.ndarray,
    a: np.ndarray,
    samplerate: float,
    target_freq: float,
    half_width_hz: float,
    point_count: int,
) -> tuple[float, float, float]:
    nyquist = samplerate / 2.0
    if half_width_hz <= 0:
        raw_delay = target_group_delay_ms(signal, b, a, samplerate, target_freq)
        delay_ms = abs(raw_delay)
        return delay_ms, delay_ms, float(target_freq)

    lower = max(np.finfo(float).eps, target_freq - half_width_hz)
    upper = min(nyquist - np.finfo(float).eps, target_freq + half_width_hz)
    if lower >= upper:
        raw_delay = target_group_delay_ms(signal, b, a, samplerate, target_freq)
        delay_ms = abs(raw_delay)
        return delay_ms, delay_ms, float(target_freq)

    point_count = max(int(point_count), 3)
    scan_freqs = np.linspace(lower, upper, point_count)
    scan_freqs = np.unique(np.concatenate([scan_freqs, np.asarray([target_freq])]))
    scan_freqs = scan_freqs[(scan_freqs > 0.0) & (scan_freqs < nyquist)]
    if scan_freqs.size == 0:
        raw_delay = target_group_delay_ms(signal, b, a, samplerate, target_freq)
        delay_ms = abs(raw_delay)
        return delay_ms, delay_ms, float(target_freq)

    delays_ms = group_delay_ms_at_frequencies(signal, b, a, samplerate, scan_freqs)
    abs_delays_ms = np.abs(delays_ms)
    finite_mask = np.isfinite(abs_delays_ms)
    if not np.any(finite_mask):
        return math.nan, math.nan, math.nan

    target_index = int(np.argmin(np.abs(scan_freqs - target_freq)))
    target_delay_ms = float(abs_delays_ms[target_index])
    if not np.isfinite(target_delay_ms):
        return math.nan, math.nan, math.nan

    finite_indexes = np.flatnonzero(finite_mask)
    peak_index = int(finite_indexes[np.argmax(abs_delays_ms[finite_indexes])])
    return (
        target_delay_ms,
        float(abs_delays_ms[peak_index]),
        float(scan_freqs[peak_index]),
    )


def db_to_amplitude_ratio(db_values):
    return np.power(10.0, np.asarray(db_values) / 20.0)


def format_amplitude_ratio(value: float) -> str:
    if value >= 10.0:
        return f"{value:.1f}x"
    if value >= 1.0:
        return f"{value:.3g}x"
    if value >= 0.001:
        return f"{value:.3f}x"
    return f"{value:.1e}x"


def sync_ratio_axis_to_db_axis(db_axis, ratio_axis):
    """右軸を左軸のdB目盛りに同期し, ラベルだけ振幅倍率にする."""
    ymin, ymax = db_axis.get_ylim()
    lower, upper = sorted((ymin, ymax))
    db_ticks = [
        float(tick)
        for tick in db_axis.get_yticks()
        if lower <= float(tick) <= upper
    ]

    ratio_axis.set_ylim(ymin, ymax)
    ratio_axis.set_yticks(db_ticks)
    ratio_axis.set_yticklabels(
        [format_amplitude_ratio(float(db_to_amplitude_ratio(tick))) for tick in db_ticks]
    )


def calc_bandwidth_and_q(frequencies: np.ndarray, db_values: np.ndarray, target_freq: float, gain_db: float):
    target_index = int(np.argmin(np.abs(frequencies - target_freq)))
    threshold = gain_db - 3.0
    above = db_values >= threshold
    if target_index >= len(above) or not above[target_index]:
        return None, None

    left = target_index
    while left > 0 and above[left - 1]:
        left -= 1

    right = target_index
    while right < len(above) - 1 and above[right + 1]:
        right += 1

    bandwidth = float(frequencies[right] - frequencies[left])
    if bandwidth <= 0:
        return None, None
    return bandwidth, float(target_freq / bandwidth)


def max_gain_near_target(
    frequencies: np.ndarray,
    db_values: np.ndarray,
    target_freq: float,
    half_width_hz: float,
    gain_at_target: float,
) -> tuple[float, float]:
    if half_width_hz <= 0:
        return float(gain_at_target), float(target_freq)

    lower = target_freq - half_width_hz
    upper = target_freq + half_width_hz
    mask = (
        (frequencies >= lower)
        & (frequencies <= upper)
        & np.isfinite(db_values)
    )
    if not np.any(mask):
        return float(gain_at_target), float(target_freq)

    indexes = np.flatnonzero(mask)
    peak_index = int(indexes[np.argmax(db_values[indexes])])
    peak_gain = float(db_values[peak_index])
    peak_freq = float(frequencies[peak_index])
    if np.isfinite(gain_at_target) and gain_at_target > peak_gain:
        return float(gain_at_target), float(target_freq)
    return peak_gain, peak_freq


def iter_candidate_edges(target_freq: float, search_width: float, edge_step: float):
    half_width = search_width / 2.0
    low_values = frange(max(0.01, target_freq - half_width), target_freq - edge_step, edge_step)
    high_values = frange(target_freq + edge_step, target_freq + half_width, edge_step)
    for fp0 in low_values:
        for fp1 in high_values:
            if fp0 < target_freq < fp1:
                yield float(fp0), float(fp1)


def iter_candidate_edges_from_offsets(target_freq: float, offset_values: list[float]):
    offsets = [float(value) for value in offset_values if float(value) > 0.0]
    for low_offset in offsets:
        for high_offset in offsets:
            fp0 = target_freq - low_offset
            fp1 = target_freq + high_offset
            if fp0 > 0.0 and fp0 < target_freq < fp1:
                yield float(fp0), float(fp1)


def build_pass_edges(args: argparse.Namespace) -> list[tuple[float, float]]:
    offset_values = parse_optional_float_list(args.passband_offset_values)
    if offset_values is not None:
        return unique_pairs(iter_candidate_edges_from_offsets(args.target_freq, offset_values))

    width_values = parse_optional_float_list(args.passband_search_width_values)
    if width_values is None:
        width_values = [args.passband_search_width]

    edge_step_values = parse_optional_float_list(args.passband_edge_step_values)
    if edge_step_values is None:
        edge_step_values = [args.passband_edge_step]

    edges = []
    for search_width in width_values:
        for edge_step in edge_step_values:
            edges.extend(iter_candidate_edges(args.target_freq, search_width, edge_step))
    return unique_pairs(edges)


def build_stop_gaps(args: argparse.Namespace) -> list[float]:
    explicit_values = parse_optional_float_list(args.stopband_gap_values)
    if explicit_values is not None:
        return unique_floats(value for value in explicit_values if value > 0.0)
    return unique_floats(frange(args.stopband_gap_min, args.stopband_gap_max, args.stopband_gap_step))


def build_families(args: argparse.Namespace) -> list[str]:
    family_text = args.families if args.families else args.family
    families = []
    for family in parse_name_list(family_text):
        if family not in SUPPORTED_FAMILIES:
            raise ValueError(f"family は {', '.join(SUPPORTED_FAMILIES)} のいずれか")
        if family not in families:
            families.append(family)
    if not families:
        families = [args.family]
    return families


def find_candidates(args: argparse.Namespace) -> list[FilterCandidate]:
    signal = require_scipy_signal()

    samplerate = args.samplerate
    target_freq = args.target_freq
    nyquist = samplerate / 2.0
    if not 0.0 < target_freq < nyquist:
        raise ValueError("target_freq は 0Hzより大きく, ナイキスト周波数より小さい必要あり")

    families = build_families(args)
    pass_edges = build_pass_edges(args)
    stop_gaps = build_stop_gaps(args)
    gpass_values = parse_float_list(args.gpass_values)
    gstop_values = parse_float_list(args.gstop_values)
    total = len(families) * len(pass_edges) * len(stop_gaps) * len(gpass_values) * len(gstop_values)
    progress_unit = max(total // 100, 1)
    candidates: list[FilterCandidate] = []

    if total == 0:
        raise ValueError("探索範囲が空, passband_search_width と passband_edge_step を確認")

    if args.progress:
        print(
            "Search space: "
            f"preset={args.search_preset}, "
            f"families={','.join(families)}, "
            f"pass_edges={len(pass_edges)}, "
            f"stop_gaps={len(stop_gaps)}, "
            f"gpass={len(gpass_values)}, "
            f"gstop={len(gstop_values)}, "
            f"total={total}"
        )

    step_count = 0
    for family in families:
        for gpass in gpass_values:
            for gstop in gstop_values:
                for stop_gap in stop_gaps:
                    for fp0, fp1 in pass_edges:
                        step_count += 1
                        if args.progress and (step_count == 1 or step_count % progress_unit == 0 or step_count == total):
                            progress = step_count / total * 100.0
                            print(f"Progress: {progress:6.2f}% ({step_count}/{total})", end="\r")

                        fs0 = fp0 - stop_gap
                        fs1 = fp1 + stop_gap
                        if fs0 <= 0.0 or fs1 >= nyquist:
                            continue
                        if not (fs0 < fp0 < target_freq < fp1 < fs1):
                            continue

                        wp = np.array([fp0 / nyquist, fp1 / nyquist], dtype=float)
                        ws = np.array([fs0 / nyquist, fs1 / nyquist], dtype=float)

                        try:
                            with warnings.catch_warnings():
                                warnings.filterwarnings("ignore", category=RuntimeWarning)
                                order, b, a = design_iir(signal, family, wp, ws, gpass, gstop)
                        except ValueError:
                            continue

                        direct_form_order = len(a) - 1
                        if (
                            args.max_direct_form_order is not None
                            and direct_form_order > args.max_direct_form_order
                        ):
                            continue

                        with warnings.catch_warnings():
                            warnings.filterwarnings("ignore", category=RuntimeWarning)
                            poles = np.roots(a)
                            pole_abs = np.abs(poles)
                            max_pole_abs = float(np.max(pole_abs)) if pole_abs.size else 0.0
                        if not np.isfinite(max_pole_abs):
                            continue
                        if max_pole_abs >= args.max_pole_abs:
                            continue

                        frequencies, db_values = response_db(signal, b, a, samplerate, args.wor_n)
                        gain_at_target = float(np.interp(target_freq, frequencies, db_values))
                        if gain_at_target < args.acceptable_gain_db:
                            continue
                        if (
                            args.max_target_gain_db is not None
                            and gain_at_target > args.max_target_gain_db
                        ):
                            continue

                        near_target_max_gain, near_target_peak_freq = max_gain_near_target(
                            frequencies,
                            db_values,
                            target_freq,
                            args.target_neighborhood_width,
                            gain_at_target,
                        )
                        if not np.isfinite(near_target_max_gain):
                            continue
                        if (
                            args.max_near_target_gain_db is not None
                            and near_target_max_gain > args.max_near_target_gain_db
                        ):
                            continue

                        bandwidth, q_value = calc_bandwidth_and_q(
                            frequencies, db_values, target_freq, gain_at_target
                        )
                        if bandwidth is None or q_value is None:
                            continue
                        if args.max_q is not None and q_value > args.max_q:
                            continue

                        delay_ms, near_delay_ms, near_delay_peak_freq = target_and_near_delay_ms(
                            signal,
                            b,
                            a,
                            samplerate,
                            target_freq,
                            args.target_delay_neighborhood_width,
                            args.target_delay_neighborhood_points,
                        )
                        if not np.isfinite(delay_ms) or not np.isfinite(near_delay_ms):
                            continue
                        if args.max_target_delay_ms is not None:
                            if delay_ms > args.max_target_delay_ms:
                                continue
                            if near_delay_ms > args.max_target_delay_ms:
                                continue

                        candidates.append(
                            FilterCandidate(
                                family=family,
                                order=int(order),
                                b=b,
                                a=a,
                                fp=(float(fp0), float(fp1)),
                                fs=(float(fs0), float(fs1)),
                                gpass=float(gpass),
                                gstop=float(gstop),
                                acceptable_gain_db=float(args.acceptable_gain_db),
                                q_value=float(q_value),
                                bandwidth_3db=float(bandwidth),
                                gain_at_target_db=gain_at_target,
                                near_target_max_gain_db=near_target_max_gain,
                                near_target_peak_freq_hz=near_target_peak_freq,
                                target_delay_ms=delay_ms,
                                near_target_max_delay_ms=near_delay_ms,
                                near_target_delay_peak_freq_hz=near_delay_peak_freq,
                                max_pole_abs=max_pole_abs,
                                frequencies=frequencies,
                                response_db=db_values,
                            )
                        )

    if args.progress:
        print()

    candidates.sort(
        key=lambda item: (
            item.q_value,
            -item.near_target_max_delay_ms,
            -abs(item.target_delay_ms),
            -item.direct_form_order,
            item.gain_at_target_db,
        ),
        reverse=True,
    )
    return unique_candidates(candidates)[: args.top_n]


def candidate_coeff_key(candidate: FilterCandidate) -> tuple[str, ...]:
    values = np.concatenate([candidate.a, candidate.b])
    return tuple(f"{float(value):.10e}" for value in values)


def unique_candidates(candidates: list[FilterCandidate]) -> list[FilterCandidate]:
    unique: list[FilterCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = candidate_coeff_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def format_tuple(values: Iterable[float], indent: str = "    ") -> str:
    lines = [f"{indent}{float(value):.18g}," for value in values]
    return "(\n" + "\n".join(lines) + "\n)"


def print_candidates(candidates: list[FilterCandidate], target_freq: float):
    if not candidates:
        print(
            "条件に合うフィルタ候補なし. "
            "探索範囲, gstop, max_q, max_pole_abs, max_direct_form_orderを緩める."
        )
        return

    for rank, candidate in enumerate(candidates, start=1):
        print(f"Rank {rank}:")
        print(f"  family: {candidate.family}")
        print(f"  prototype_order: {candidate.order}")
        print(f"  direct_form_order: {candidate.direct_form_order}")
        print(f"  fp: {candidate.fp[0]:.4f} Hz - {candidate.fp[1]:.4f} Hz")
        print(f"  fs: {candidate.fs[0]:.4f} Hz - {candidate.fs[1]:.4f} Hz")
        print(f"  gpass: {candidate.gpass:.2f} dB")
        print(f"  gstop: {candidate.gstop:.2f} dB")
        print(f"  gain_at_{target_freq:g}Hz: {candidate.gain_at_target_db:.2f} dB")
        print(
            "  near_target_max_gain: "
            f"{candidate.near_target_max_gain_db:.2f} dB "
            f"at {candidate.near_target_peak_freq_hz:.4f} Hz"
        )
        print(f"  bandwidth_3db: {candidate.bandwidth_3db:.4f} Hz")
        print(f"  Q: {candidate.q_value:.2f}")
        print(f"  target_delay: {candidate.target_delay_ms:.2f} ms")
        print(
            "  near_target_max_delay: "
            f"{candidate.near_target_max_delay_ms:.2f} ms "
            f"at {candidate.near_target_delay_peak_freq_hz:.4f} Hz"
        )
        print(f"  max_pole_abs: {candidate.max_pole_abs:.8f}")
        print()

    best = candidates[0]
    print("PhaseTiming.py へ貼る係数:")
    print("DEFAULT_BANDPASS_A = " + format_tuple(best.a))
    print()
    print("DEFAULT_BANDPASS_B = " + format_tuple(best.b))


def save_candidates(path: Path, args: argparse.Namespace, candidates: list[FilterCandidate]):
    payload = {
        "settings": {
            "samplerate": args.samplerate,
            "target_freq": args.target_freq,
            "search_preset": args.search_preset,
            "family": args.family,
            "families": build_families(args),
            "passband_search_width": args.passband_search_width,
            "passband_edge_step": args.passband_edge_step,
            "passband_offset_values": parse_optional_float_list(args.passband_offset_values),
            "passband_search_width_values": parse_optional_float_list(args.passband_search_width_values),
            "passband_edge_step_values": parse_optional_float_list(args.passband_edge_step_values),
            "stopband_gap_min": args.stopband_gap_min,
            "stopband_gap_max": args.stopband_gap_max,
            "stopband_gap_step": args.stopband_gap_step,
            "stopband_gap_values": parse_optional_float_list(args.stopband_gap_values),
            "gpass_values": parse_float_list(args.gpass_values),
            "gstop_values": parse_float_list(args.gstop_values),
            "acceptable_gain_db": args.acceptable_gain_db,
            "max_target_gain_db": args.max_target_gain_db,
            "target_neighborhood_width": args.target_neighborhood_width,
            "max_near_target_gain_db": args.max_near_target_gain_db,
            "max_q": args.max_q,
            "max_target_delay_ms": args.max_target_delay_ms,
            "target_delay_neighborhood_width": args.target_delay_neighborhood_width,
            "target_delay_neighborhood_points": args.target_delay_neighborhood_points,
            "max_pole_abs": args.max_pole_abs,
            "max_direct_form_order": args.max_direct_form_order,
        },
        "candidates": [candidate.to_json_dict() for candidate in candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def draw_candidate_overview(candidates: list[FilterCandidate], args: argparse.Namespace, ax_response, ax_delay):
    signal = require_scipy_signal()

    for rank, candidate in enumerate(candidates, start=1):
        mask = candidate.frequencies <= args.plot_max_freq
        label = (
            f"Rank {rank}, Q={candidate.q_value:.1f}, "
            f"order={candidate.direct_form_order}, "
            f"target={candidate.gain_at_target_db:.1f} dB, "
            f"near={candidate.near_target_max_gain_db:.1f} dB, "
            f"near delay={candidate.near_target_max_delay_ms:.0f} ms"
        )
        ax_response.plot(candidate.frequencies[mask], candidate.response_db[mask], label=label)

        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=".*denominator is extremely small.*",
                    category=UserWarning,
                )
                w, delay_samples = signal.group_delay((candidate.b, candidate.a), w=args.wor_n)
            delay_freq = w / (2.0 * np.pi) * args.samplerate
            delay_ms = delay_samples / args.samplerate * 1000.0
            delay_mask = delay_freq <= args.plot_max_freq
            target_delay = float(np.interp(args.target_freq, delay_freq, delay_ms))
            ax_delay.plot(
                delay_freq[delay_mask],
                delay_ms[delay_mask],
                label=(
                    f"Rank {rank}, target delay={target_delay:.1f} ms, "
                    f"near max={candidate.near_target_max_delay_ms:.1f} ms"
                ),
            )
        except Exception as exc:  # scipyの数値警告で失敗する候補あり.
            print(f"Rank {rank} の群遅延表示をスキップ: {exc}")

    ax_response.axvline(args.target_freq, color="black", linestyle="--", linewidth=1.0, label="target")
    ax_response.axhline(-3.0, color="gray", linestyle=":", linewidth=1.0)
    ax_response.set_title("Frequency response")
    ax_response.set_xlabel("Frequency [Hz]")
    ax_response.set_ylabel("Gain [dB]")
    ax_response.set_xlim(0, args.plot_max_freq)
    ax_response.grid(True)
    ax_response.legend(loc="best")
    ratio_axis = ax_response.twinx()
    ratio_axis.set_ylabel("Amplitude ratio")
    sync_ratio_axis_to_db_axis(ax_response, ratio_axis)
    ax_response.callbacks.connect(
        "ylim_changed",
        lambda axis: sync_ratio_axis_to_db_axis(axis, ratio_axis),
    )

    ax_delay.axvline(args.target_freq, color="black", linestyle="--", linewidth=1.0)
    ax_delay.set_title("Group delay")
    ax_delay.set_xlabel("Frequency [Hz]")
    ax_delay.set_ylabel("Delay [ms]")
    ax_delay.set_xlim(0, args.plot_max_freq)
    ax_delay.grid(True)
    ax_delay.legend(loc="best")


def plot_candidates(candidates: list[FilterCandidate], args: argparse.Namespace):
    if not candidates or args.no_plot:
        return

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib が見つからないためグラフ表示をスキップ.")
        return

    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    draw_candidate_overview(candidates, args, axes[0], axes[1])
    fig.subplots_adjust(left=0.08, right=0.92, bottom=0.08, top=0.94, hspace=0.35)

    if args.save_figure:
        save_path = Path(args.save_figure)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Figure saved: {save_path}")

    enable_rank_check_panel(fig, fig.axes)
    plt.show(block=True)


def is_non_interactive_matplotlib_backend(backend_name: str) -> bool:
    backend = backend_name.lower()
    return backend in {
        "agg",
        "pdf",
        "ps",
        "svg",
        "cairo",
        "template",
    }


def enable_interactive_legends(fig, axes):
    """凡例クリックで対応する線や点を表示/非表示にする."""
    pick_map = {}
    axes_list = np.ravel(np.asarray(axes, dtype=object)).tolist()

    for ax in axes_list:
        legend = ax.get_legend()
        if legend is None:
            continue

        original_handles, _ = ax.get_legend_handles_labels()
        legend_handles = getattr(legend, "legend_handles", None)
        if legend_handles is None:
            legend_handles = getattr(legend, "legendHandles", [])
        legend_texts = legend.get_texts()
        item_count = min(len(original_handles), len(legend_handles), len(legend_texts))

        for index in range(item_count):
            original = original_handles[index]
            legend_items = [legend_handles[index], legend_texts[index]]
            try:
                original.set_picker(8)
                if hasattr(original, "set_pickradius"):
                    original.set_pickradius(8)
                pick_map[original] = (original, legend_items)
            except Exception:
                pass
            for item in legend_items:
                item.set_picker(8)
                if hasattr(item, "set_pickradius"):
                    item.set_pickradius(8)
                pick_map[item] = (original, legend_items)

    if not pick_map:
        return

    old_cid = getattr(fig, "_interactive_legend_cid", None)
    if old_cid is not None:
        fig.canvas.mpl_disconnect(old_cid)

    def on_pick(event):
        picked = event.artist
        if picked not in pick_map:
            return

        original, legend_items = pick_map[picked]
        visible = not original.get_visible()
        original.set_visible(visible)
        alpha = 1.0 if visible else 0.2
        for item in legend_items:
            item.set_alpha(alpha)
        fig.canvas.draw_idle()

    fig._interactive_legend_map = pick_map
    fig._interactive_legend_cid = fig.canvas.mpl_connect("pick_event", on_pick)


def visibility_control_label(label: str) -> str:
    text = str(label).strip()
    parts = text.split()
    if len(parts) >= 2 and parts[0] == "Rank":
        rank_text = parts[1].rstrip(",")
        if rank_text.isdigit():
            return f"R{rank_text}"
    return ""


def enable_interactive_legends(fig, axes):
    """Add check buttons above the figure to show or hide plotted lines."""
    try:
        from matplotlib.widgets import CheckButtons
    except Exception:
        return

    artist_groups = {}
    legend_groups = {}
    axes_list = np.ravel(np.asarray(axes, dtype=object)).tolist()

    for ax in axes_list:
        legend = ax.get_legend()
        if legend is None:
            continue

        original_handles, _ = ax.get_legend_handles_labels()
        legend_handles = getattr(legend, "legend_handles", None)
        if legend_handles is None:
            legend_handles = getattr(legend, "legendHandles", [])
        legend_texts = legend.get_texts()
        item_count = min(len(original_handles), len(legend_handles), len(legend_texts))

        for index in range(item_count):
            label = visibility_control_label(legend_texts[index].get_text())
            if not label or label.startswith("_"):
                continue
            original = original_handles[index]
            legend_items = [legend_handles[index], legend_texts[index]]
            artist_groups.setdefault(label, []).append(original)
            legend_groups.setdefault(label, []).extend(legend_items)

    if not artist_groups:
        return

    labels = list(artist_groups.keys())
    max_rows = 2
    column_count = int(math.ceil(len(labels) / max_rows))
    row_count = min(max_rows, len(labels))
    panel_height = 0.075 if row_count <= 1 else 0.105
    plot_top = 1.0 - panel_height - 0.012
    fig.subplots_adjust(top=plot_top)

    checkbuttons = []
    column_width = 0.94 / column_count
    panel_bottom = plot_top + 0.01
    panel_inner_height = panel_height - 0.015

    def set_group_visible(label, visible):
        for artist in artist_groups[label]:
            artist.set_visible(visible)
        alpha = 1.0 if visible else 0.25
        for item in legend_groups.get(label, []):
            item.set_alpha(alpha)
        fig.canvas.draw_idle()

    for column_index in range(column_count):
        start = column_index * max_rows
        column_labels = labels[start : start + max_rows]
        if not column_labels:
            continue

        ax_box = fig.add_axes(
            [
                0.03 + column_index * column_width,
                panel_bottom,
                column_width - 0.006,
                panel_inner_height,
            ]
        )
        status = [all(artist.get_visible() for artist in artist_groups[label]) for label in column_labels]
        buttons = CheckButtons(ax_box, column_labels, status)
        for text in buttons.labels:
            text.set_fontsize(9)

        def on_clicked(label):
            new_visible = not all(artist.get_visible() for artist in artist_groups[label])
            set_group_visible(label, new_visible)

        buttons.on_clicked(on_clicked)
        checkbuttons.append(buttons)

    fig._visibility_checkbox_groups = artist_groups
    fig._visibility_checkbuttons = checkbuttons


def enable_interactive_legends(fig, axes):
    """Toggle same-rank plotted lines by clicking legend labels."""
    legend_pick_map = {}
    rank_groups = {}
    legend_groups = {}
    axes_list = np.ravel(np.asarray(axes, dtype=object)).tolist()

    for ax in axes_list:
        legend = ax.get_legend()
        if legend is None:
            continue

        original_handles, _ = ax.get_legend_handles_labels()
        legend_handles = getattr(legend, "legend_handles", None)
        if legend_handles is None:
            legend_handles = getattr(legend, "legendHandles", [])
        legend_texts = legend.get_texts()
        item_count = min(len(original_handles), len(legend_handles), len(legend_texts))

        for index in range(item_count):
            rank_label = visibility_control_label(legend_texts[index].get_text())
            if not rank_label:
                continue

            original = original_handles[index]
            legend_items = [legend_handles[index], legend_texts[index]]
            rank_groups.setdefault(rank_label, []).append(original)
            legend_groups.setdefault(rank_label, []).extend(legend_items)

            legend_pick_map[legend_texts[index]] = rank_label

    if not rank_groups:
        return

    old_pick_cid = getattr(fig, "_visibility_legend_pick_cid", None)
    if old_pick_cid is not None:
        fig.canvas.mpl_disconnect(old_pick_cid)
    old_click_cid = getattr(fig, "_visibility_legend_click_cid", None)
    if old_click_cid is not None:
        fig.canvas.mpl_disconnect(old_click_cid)

    def set_rank_visible(rank_label, visible):
        for artist in rank_groups[rank_label]:
            artist.set_visible(visible)
        alpha = 1.0 if visible else 0.25
        for item in legend_groups.get(rank_label, []):
            item.set_alpha(alpha)
        fig.canvas.draw_idle()

    def toggle_rank(rank_label):
        new_visible = not all(artist.get_visible() for artist in rank_groups[rank_label])
        set_rank_visible(rank_label, new_visible)

    def on_button_press(event):
        if event.button != 1:
            return
        try:
            renderer = fig.canvas.get_renderer()
        except Exception:
            return
        for item, rank_label in legend_pick_map.items():
            try:
                if item.get_window_extent(renderer).contains(event.x, event.y):
                    toggle_rank(rank_label)
                    return
            except Exception:
                continue

    fig._visibility_legend_groups = rank_groups
    fig._visibility_legend_items = legend_groups
    fig._visibility_legend_pick_map = legend_pick_map
    fig._visibility_legend_pick_cid = None
    fig._visibility_legend_click_cid = fig.canvas.mpl_connect("button_press_event", on_button_press)


def rank_label_sort_key(label: str):
    if label.startswith("R") and label[1:].isdigit():
        return (0, int(label[1:]))
    return (1, label)


def enable_rank_check_panel(fig, axes):
    """Add a left-side checkbox panel to show or hide each Rank."""
    try:
        from matplotlib.widgets import CheckButtons
    except Exception:
        return

    rank_groups = {}
    legend_groups = {}
    axes_list = np.ravel(np.asarray(axes, dtype=object)).tolist()

    for ax in axes_list:
        legend = ax.get_legend()
        if legend is None:
            continue

        original_handles, _ = ax.get_legend_handles_labels()
        legend_handles = getattr(legend, "legend_handles", None)
        if legend_handles is None:
            legend_handles = getattr(legend, "legendHandles", [])
        legend_texts = legend.get_texts()
        item_count = min(len(original_handles), len(legend_handles), len(legend_texts))

        for index in range(item_count):
            rank_label = visibility_control_label(legend_texts[index].get_text())
            if not rank_label:
                continue
            rank_groups.setdefault(rank_label, []).append(original_handles[index])
            legend_groups.setdefault(rank_label, []).extend([legend_handles[index], legend_texts[index]])

    if not rank_groups:
        return

    labels = sorted(rank_groups.keys(), key=rank_label_sort_key)
    max_rows = 16
    column_count = int(math.ceil(len(labels) / max_rows))
    panel_width = min(0.20, 0.075 * column_count + 0.025)
    fig.subplots_adjust(left=panel_width + 0.04, right=0.97, top=0.92, bottom=0.08)

    checkbuttons = []
    column_width = (panel_width - 0.02) / column_count

    def set_rank_visible(rank_label, visible):
        for artist in rank_groups[rank_label]:
            artist.set_visible(visible)
        alpha = 1.0 if visible else 0.25
        for item in legend_groups.get(rank_label, []):
            item.set_alpha(alpha)
        fig.canvas.draw_idle()

    for column_index in range(column_count):
        start = column_index * max_rows
        column_labels = labels[start : start + max_rows]
        if not column_labels:
            continue

        ax_box = fig.add_axes(
            [
                0.015 + column_index * column_width,
                0.10,
                column_width - 0.006,
                0.80,
            ]
        )
        ax_box.set_title("line", fontsize=9)
        status = [all(artist.get_visible() for artist in rank_groups[label]) for label in column_labels]
        buttons = CheckButtons(ax_box, column_labels, status)
        for text in buttons.labels:
            text.set_fontsize(9)

        def on_clicked(label):
            new_visible = not all(artist.get_visible() for artist in rank_groups[label])
            set_rank_visible(label, new_visible)

        buttons.on_clicked(on_clicked)
        checkbuttons.append(buttons)

    fig._rank_check_groups = rank_groups
    fig._rank_check_buttons = checkbuttons


def ask_text(label: str, default):
    if default is None:
        prompt = f"{label}: "
    else:
        prompt = f"{label} [{default}]: "
    value = input(prompt).strip()
    return default if value == "" else value


def ask_float(label: str, default: float | None, allow_none: bool = False):
    while True:
        value = ask_text(label, "" if default is None else default)
        if value == "" and allow_none:
            return None
        try:
            parsed = float(value)
        except ValueError:
            print("数値で入力.")
            continue
        if allow_none and parsed <= 0:
            return None
        return parsed


def ask_int(label: str, default: int | None, allow_none: bool = False):
    while True:
        value = ask_text(label, "" if default is None else default)
        if value == "" and allow_none:
            return None
        try:
            parsed = int(value)
        except ValueError:
            print("整数で入力.")
            continue
        if allow_none and parsed <= 0:
            return None
        return parsed


def ask_bool(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if value == "":
            return default
        if value in ("y", "yes", "1"):
            return True
        if value in ("n", "no", "0"):
            return False
        print("y または n で入力.")


def ask_choice(label: str, choices: list[str], default: str) -> str:
    while True:
        print(label)
        for index, choice in enumerate(choices, start=1):
            marker = " *" if choice == default else ""
            print(f"  {index}: {choice}{marker}")
        value = input(f"番号または名前 [{default}]: ").strip()
        if value == "":
            return default
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(choices):
                return choices[index - 1]
        if value in choices:
            return value
        print("候補から選択.")


def ask_path(label: str, default: Path | None = None) -> Path | None:
    value = ask_text(label, "" if default is None else default)
    if value == "":
        return default
    return Path(value)


def apply_interactive_inputs(args: argparse.Namespace):
    print("ピークフィルタ設計, Enterで既定値を使用.")
    args.samplerate = ask_float("サンプリング周波数[Hz]", args.samplerate)
    args.target_freq = ask_float("強調する周波数[Hz]", args.target_freq)
    args.search_preset = ask_choice("探索プリセット", ["standard", "sharp", "exhaustive"], args.search_preset)
    args.family = ask_choice("IIR設計法", ["butter", "cheby1", "cheby2", "ellip"], args.family)

    edit_detail = ask_bool("詳細設定を変更する", False)
    if edit_detail:
        args.families = ask_text("探索するIIR設計法, カンマ区切り", args.families or args.family)
        args.passband_offset_values = ask_text(
            "targetから通過帯域端までの距離候補[Hz], カンマ区切り, 空なら通常探索",
            args.passband_offset_values or "",
        )
        if args.passband_offset_values == "":
            args.passband_offset_values = None
        args.stopband_gap_values = ask_text(
            "stopband gap候補[Hz], カンマ区切り, 空ならmin/max/step",
            args.stopband_gap_values or "",
        )
        if args.stopband_gap_values == "":
            args.stopband_gap_values = None
        args.passband_search_width = ask_float("通過帯域候補の探索幅[Hz]", args.passband_search_width)
        args.passband_edge_step = ask_float("通過帯域端候補の刻み幅[Hz]", args.passband_edge_step)
        args.stopband_gap_min = ask_float("阻止帯ギャップ最小[Hz]", args.stopband_gap_min)
        args.stopband_gap_max = ask_float("阻止帯ギャップ最大[Hz]", args.stopband_gap_max)
        args.stopband_gap_step = ask_float("阻止帯ギャップ刻み[Hz]", args.stopband_gap_step)
        args.gpass_values = ask_text("通過域端最大損失[dB], カンマ区切り", args.gpass_values)
        args.gstop_values = ask_text("阻止域端最小減衰[dB], カンマ区切り", args.gstop_values)
        args.acceptable_gain_db = ask_float("target周波数の許容最小ゲイン[dB]", args.acceptable_gain_db)
        args.max_target_gain_db = ask_float(
            "target周波数の許容最大ゲイン[dB], 0で制限なし",
            args.max_target_gain_db,
            allow_none=True,
        )
        args.target_neighborhood_width = ask_float(
            "target周波数周辺ゲイン確認幅[Hz], target±幅",
            args.target_neighborhood_width,
        )
        args.max_near_target_gain_db = ask_float(
            "target周波数周辺の許容最大ゲイン[dB], 0で制限なし",
            args.max_near_target_gain_db,
            allow_none=True,
        )
        args.max_q = ask_float("Q値上限, 0で制限なし", args.max_q, allow_none=True)
        args.max_target_delay_ms = ask_float(
            "target周波数の群遅延上限[ms], 0で制限なし",
            args.max_target_delay_ms,
            allow_none=True,
        )
        args.target_delay_neighborhood_width = ask_float(
            "群遅延上限を確認するtarget周波数周辺幅[Hz], target±幅",
            args.target_delay_neighborhood_width,
        )
        args.target_delay_neighborhood_points = ask_int(
            "群遅延周辺確認の走査点数",
            args.target_delay_neighborhood_points,
        )
        args.max_pole_abs = ask_float("極の絶対値上限", args.max_pole_abs)
        args.max_direct_form_order = ask_int(
            "a,b直接形の最大次数, 0で制限なし",
            args.max_direct_form_order,
            allow_none=True,
        )
        args.top_n = ask_int("表示する候補数", args.top_n)
        args.plot_max_freq = ask_float("グラフ表示の最大周波数[Hz]", args.plot_max_freq)

    args.no_plot = not ask_bool("グラフを表示する", not args.no_plot)
    args.output = ask_path("候補と係数をJSON保存するパス, 空なら保存なし", args.output)
    if not args.no_plot:
        args.save_figure = ask_text("グラフ画像保存パス, 空なら保存なし", args.save_figure or "")
        if args.save_figure == "":
            args.save_figure = None
    args.no_check_filter = not ask_bool("候補をcheck_filter.pyで自動確認する", not args.no_check_filter)
    if not args.no_check_filter:
        args.check_ranks = ask_text("自動確認するRank. all, 1, 1-10, 1,3,5 など", args.check_ranks)
        args.detail_ranks = ask_text("1枚まとめ表示に出す詳細Rank. all, 1, 1-10, 1,3,5 など", args.detail_ranks)


def apply_positional_args(args: argparse.Namespace):
    values = getattr(args, "positional", [])
    if not values:
        return
    if len(values) > 3:
        raise ValueError("位置引数は target_freq, samplerate, family の最大3つ")
    args.target_freq = float(values[0])
    if len(values) >= 2:
        args.samplerate = float(values[1])
    if len(values) >= 3:
        if values[2] not in ("butter", "cheby1", "cheby2", "ellip"):
            raise ValueError("family は butter, cheby1, cheby2, ellip のいずれか")
        args.family = values[2]


def option_supplied(argv: list[str], names: Iterable[str]) -> bool:
    option_names = tuple(names)
    for item in argv:
        for name in option_names:
            if item == name or item.startswith(f"{name}="):
                return True
    return False


def positional_family_supplied(args: argparse.Namespace) -> bool:
    return len(getattr(args, "positional", [])) >= 3


def apply_search_preset(args: argparse.Namespace, raw_argv: list[str]):
    preset = SEARCH_PRESET_OVERRIDES.get(args.search_preset)
    if preset is None:
        return

    if (
        not option_supplied(raw_argv, ["--families"])
        and not option_supplied(raw_argv, ["--family"])
        and not positional_family_supplied(args)
    ):
        args.families = preset["families"]
    if (
        not option_supplied(raw_argv, ["--passband-offset-values"])
        and not option_supplied(raw_argv, ["--passband-search-width-values"])
        and not option_supplied(raw_argv, ["--passband-edge-step-values"])
    ):
        args.passband_offset_values = preset["passband_offset_values"]
    if not option_supplied(raw_argv, ["--stopband-gap-values"]):
        args.stopband_gap_values = preset["stopband_gap_values"]
    if not option_supplied(raw_argv, ["--gpass-values"]):
        args.gpass_values = preset["gpass_values"]
    if not option_supplied(raw_argv, ["--gstop-values"]):
        args.gstop_values = preset["gstop_values"]
    if not option_supplied(raw_argv, ["--top-n"]):
        args.top_n = max(args.top_n, int(preset["top_n"]))


def parse_rank_selection(text: str, candidate_count: int) -> list[int]:
    text = (text or "all").strip().lower()
    if text == "all":
        return list(range(1, candidate_count + 1))

    ranks: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            ranks.update(range(start, end + 1))
        else:
            ranks.add(int(part))

    selected = sorted(rank for rank in ranks if 1 <= rank <= candidate_count)
    if not selected:
        raise ValueError("check-ranks の指定に有効なRankがない")
    return selected


def rank_output_path(path_text: str | None, rank: int, selected_count: int) -> str | None:
    if not path_text:
        return None
    path = Path(path_text)
    if selected_count <= 1:
        return str(path)
    return str(path.with_name(f"{path.stem}_rank{rank:02d}{path.suffix}"))


def build_check_args(args: argparse.Namespace, save_figure: str | None = None, save_info: str | None = None):
    import check_filter

    check_test_freqs = args.check_test_freqs or str(args.target_freq)
    check_freq_count = len(parse_float_list(check_test_freqs))
    check_test_amps = args.check_test_amps or ",".join(["1"] * check_freq_count)
    check_argv = [
        "--samplerate",
        str(args.samplerate),
        "--target-freq",
        str(args.target_freq),
        "--plot-max-freq",
        str(args.plot_max_freq),
        "--test-freqs",
        check_test_freqs,
        "--test-amps",
        check_test_amps,
    ]
    if save_figure:
        check_argv.extend(["--save-figure", save_figure])
    if save_info:
        check_argv.extend(["--save-info", save_info])
    if args.check_no_show:
        check_argv.append("--no-show")

    check_args = check_filter.build_parser().parse_args(check_argv)
    check_filter.validate_args(check_args)
    return check_args


def draw_filter_details_overlay(
    candidates: list[FilterCandidate],
    detail_ranks: list[int],
    args: argparse.Namespace,
    axes,
):
    import check_filter
    from scipy import signal

    check_args = build_check_args(args)
    test_freqs = check_filter.parse_float_list(check_args.test_freqs) if check_args.test_freqs else [check_args.target_freq]
    test_amps = check_filter.parse_float_list(check_args.test_amps) if check_args.test_amps else [1.0] * len(test_freqs)
    if len(test_amps) != len(test_freqs):
        raise ValueError("--check-test-freqs と --check-test-amps の個数を一致させてください")

    duration_sec = check_args.duration_ms / 1000.0
    time_sec, x = check_filter.build_test_signal(check_args.samplerate, duration_sec, test_freqs, test_amps)
    plot_start_sec = check_args.settle_ms / 1000.0
    plot_end_sec = min(duration_sec, plot_start_sec + check_args.plot_window_ms / 1000.0)
    mask_time = (time_sec >= plot_start_sec) & (time_sec <= plot_end_sec)

    ax_response, ax_delay, ax_zplane, ax_time = axes

    color_cycle = [f"C{index}" for index in range(max(10, len(detail_ranks)))]

    summaries: list[str] = []
    z_limit = 1.1

    for color_index, rank in enumerate(detail_ranks):
        candidate = candidates[rank - 1]
        b = candidate.b
        a = candidate.a
        color = color_cycle[color_index % len(color_cycle)]
        frequencies, response_db, _ = check_filter.compute_frequency_response(
            b, a, check_args.samplerate, check_args.wor_n
        )
        delay_freq, delay_samples, delay_ms = check_filter.compute_group_delay(
            b, a, check_args.samplerate, check_args.wor_n
        )
        zeros, poles, max_pole_abs, is_stable = check_filter.compute_poles_zeros(b, a)
        y = signal.lfilter(b, a, x)
        mask_freq = frequencies <= check_args.plot_max_freq
        mask_delay = (delay_freq <= check_args.plot_max_freq) & np.isfinite(delay_ms) & np.isfinite(delay_samples)
        gain_target_db = check_filter.nearest_value(frequencies, response_db, check_args.target_freq)
        delay_target_ms = abs(check_filter.nearest_value(delay_freq, delay_ms, check_args.target_freq))

        ax_response.plot(
            frequencies[mask_freq],
            response_db[mask_freq],
            color=color,
            label=f"Rank {rank}, Q={candidate.q_value:.1f}, target={gain_target_db:.1f} dB",
        )
        ax_delay.plot(
            delay_freq[mask_delay],
            delay_ms[mask_delay],
            color=color,
            label=f"Rank {rank}, target delay={delay_target_ms:.1f} ms",
        )
        if zeros.size:
            ax_zplane.scatter(
                np.real(zeros),
                np.imag(zeros),
                marker="o",
                facecolors="none",
                edgecolors=color,
                alpha=0.75,
            )
        if poles.size:
            ax_zplane.scatter(
                np.real(poles),
                np.imag(poles),
                marker="x",
                color=color,
                alpha=0.85,
                label=f"Rank {rank} poles, max={max_pole_abs:.5f}",
            )
        if zeros.size or poles.size:
            all_points = np.concatenate([zeros, poles]) if zeros.size and poles.size else (zeros if zeros.size else poles)
            z_limit = max(z_limit, math.ceil(float(np.max(np.abs(all_points))) * 10.0) / 10.0 + 0.1)

        ax_time.plot(
            time_sec[mask_time] * 1000.0,
            y[mask_time],
            color=color,
            label=f"Rank {rank} output",
        )
        stable_text = "stable" if is_stable else "unstable"
        summaries.append(
            f"Rank {rank}: target gain={gain_target_db:.2f} dB, "
            f"|delay|={delay_target_ms:.2f} ms, max |pole|={max_pole_abs:.8f}, {stable_text}"
        )

    ax_response.axhline(-3.0, linestyle=":", linewidth=1.0, label="-3 dB")
    for freq in test_freqs:
        is_target = np.isclose(freq, check_args.target_freq)
        label_prefix = "target" if is_target else "test"
        linestyle = "--" if is_target else ":"
        ax_response.axvline(
            freq,
            linestyle=linestyle,
            linewidth=1.0,
            label=f"{label_prefix} {freq:g} Hz",
        )
    ax_response.set_title(f"Detail overlay: frequency response ({len(detail_ranks)} ranks)")
    ax_response.set_xlabel("Frequency [Hz]")
    ax_response.set_ylabel("Gain [dB]")
    ax_response.set_xlim(0.0, check_args.plot_max_freq)
    ax_response.grid(True)
    ax_response.legend(loc="best")
    ratio_axis = ax_response.twinx()
    ratio_axis.set_ylabel("Amplitude ratio")
    check_filter.sync_ratio_axis_to_db_axis(ax_response, ratio_axis)
    ax_response.callbacks.connect(
        "ylim_changed",
        lambda axis: check_filter.sync_ratio_axis_to_db_axis(axis, ratio_axis),
    )

    for freq in test_freqs:
        is_target = np.isclose(freq, check_args.target_freq)
        label_prefix = "target" if is_target else "test"
        linestyle = "--" if is_target else ":"
        ax_delay.axvline(
            freq,
            linestyle=linestyle,
            linewidth=1.0,
            label=f"{label_prefix} {freq:g} Hz",
        )
    ax_delay.set_title(f"Detail overlay: group delay ({len(detail_ranks)} ranks)")
    ax_delay.set_xlabel("Frequency [Hz]")
    ax_delay.set_ylabel("Delay [ms]")
    ax_delay.set_xlim(0.0, check_args.plot_max_freq)
    ax_delay.grid(True)
    ax_delay.legend(loc="best")

    theta = np.linspace(0.0, 2.0 * np.pi, 720)
    ax_zplane.plot(np.cos(theta), np.sin(theta), linestyle="--", linewidth=1.0, label="Unit circle")
    ax_zplane.axhline(0.0, linewidth=0.8)
    ax_zplane.axvline(0.0, linewidth=0.8)
    ax_zplane.set_title("Detail overlay: pole-zero plot")
    ax_zplane.set_xlabel("Real")
    ax_zplane.set_ylabel("Imaginary")
    ax_zplane.set_xlim(-z_limit, z_limit)
    ax_zplane.set_ylim(-z_limit, z_limit)
    ax_zplane.set_aspect("equal", adjustable="box")
    ax_zplane.grid(True)
    ax_zplane.legend(loc="best")

    component_text = "+".join(f"{freq:g}Hz" for freq in test_freqs)
    ax_time.plot(time_sec[mask_time] * 1000.0, x[mask_time], label=f"Input ({component_text})")
    ax_time.set_title(f"Detail overlay: time waveform ({len(detail_ranks)} ranks)")
    ax_time.set_xlabel("Time [ms]")
    ax_time.set_ylabel("Amplitude")
    ax_time.grid(True)
    ax_time.legend(loc="best")

    print("Detail overlay summary:")
    print("\n".join(summaries))


def plot_combined_sheet(candidates: list[FilterCandidate], args: argparse.Namespace) -> bool:
    if not candidates or args.no_plot or args.no_check_filter:
        return False

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib が見つからないためグラフ表示をスキップ.")
        return False

    detail_ranks = parse_rank_selection(args.detail_ranks, len(candidates))
    fig = plt.figure(figsize=(16, 10))
    grid = fig.add_gridspec(2, 2)
    fig.suptitle(f"Peak filter detail overlays ({args.detail_ranks})")
    detail_axes = (
        fig.add_subplot(grid[0, 0]),
        fig.add_subplot(grid[0, 1]),
        fig.add_subplot(grid[1, 0]),
        fig.add_subplot(grid[1, 1]),
    )
    draw_filter_details_overlay(candidates, detail_ranks, args, detail_axes)

    if args.save_figure:
        save_path = Path(args.save_figure)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Figure saved: {save_path}")

    enable_rank_check_panel(fig, fig.axes)
    if is_non_interactive_matplotlib_backend(plt.get_backend()):
        plt.close(fig)
    else:
        plt.show(block=True)
    return True


def run_check_filters(candidates: list[FilterCandidate], args: argparse.Namespace):
    if args.no_check_filter:
        return

    try:
        import check_filter
    except Exception as exc:
        print(f"check_filter.py の読み込みをスキップ: {exc}")
        return

    selected_ranks = parse_rank_selection(args.check_ranks, len(candidates))
    check_test_freqs = args.check_test_freqs or str(args.target_freq)
    check_freq_count = len(parse_float_list(check_test_freqs))
    check_test_amps = args.check_test_amps or ",".join(["1"] * check_freq_count)

    for rank in selected_ranks:
        candidate = candidates[rank - 1]
        print(f"check_filter.py auto check: Rank {rank}/{len(candidates)}")
        check_argv = [
            "--samplerate",
            str(args.samplerate),
            "--target-freq",
            str(args.target_freq),
            "--plot-max-freq",
            str(args.plot_max_freq),
            "--test-freqs",
            check_test_freqs,
            "--test-amps",
            check_test_amps,
        ]
        figure_path = rank_output_path(args.check_save_figure, rank, len(selected_ranks))
        info_path = rank_output_path(args.check_save_info, rank, len(selected_ranks))
        if figure_path:
            check_argv.extend(["--save-figure", figure_path])
        if info_path:
            check_argv.extend(["--save-info", info_path])
        if args.check_no_show:
            check_argv.append("--no-show")

        check_args = check_filter.build_parser().parse_args(check_argv)
        check_filter.validate_args(check_args)
        metadata = candidate.to_json_dict()
        metadata["source"] = "design_peak_filter.py auto check"
        metadata["rank"] = rank
        check_filter.plot_all(candidate.b, candidate.a, check_args, metadata)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="狙った刺激周波数を強調するピークフィルタを探索, 設計する."
    )
    parser.add_argument(
        "positional",
        nargs="*",
        help="省略指定: target_freq [samplerate] [butter|cheby1|cheby2|ellip]",
    )
    parser.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--target-freq", type=float, default=DEFAULT_TARGET_FREQ)
    parser.add_argument(
        "--family",
        choices=SUPPORTED_FAMILIES,
        default="butter",
        help="IIR設計法.",
    )
    parser.add_argument(
        "--search-preset",
        choices=["standard", "sharp", "exhaustive"],
        default=DEFAULT_SEARCH_PRESET,
        help="探索プリセット. sharp/exhaustiveは複数変数を総当たり.",
    )
    parser.add_argument(
        "--bruteforce",
        dest="search_preset",
        action="store_const",
        const="sharp",
        help="--search-preset sharp と同じ.",
    )
    parser.add_argument(
        "--families",
        default=DEFAULT_FAMILIES,
        help="探索するIIR設計法, カンマ区切り. 例: butter,cheby2,ellip",
    )
    parser.add_argument("--passband-search-width", type=float, default=DEFAULT_PASSBAND_SEARCH_WIDTH)
    parser.add_argument("--passband-edge-step", type=float, default=DEFAULT_PASSBAND_EDGE_STEP)
    parser.add_argument(
        "--passband-offset-values",
        default=DEFAULT_PASSBAND_OFFSET_VALUES,
        help="targetから通過帯域端までの距離候補[Hz]. 指定時は左右オフセットを総当たり.",
    )
    parser.add_argument(
        "--passband-search-width-values",
        default=DEFAULT_PASSBAND_SEARCH_WIDTH_VALUES,
        help="通過帯域候補の探索幅を複数振る候補[Hz], カンマ区切り.",
    )
    parser.add_argument(
        "--passband-edge-step-values",
        default=DEFAULT_PASSBAND_EDGE_STEP_VALUES,
        help="通過帯域端刻みを複数振る候補[Hz], カンマ区切り.",
    )
    parser.add_argument("--stopband-gap-min", type=float, default=DEFAULT_STOPBAND_GAP_MIN)
    parser.add_argument("--stopband-gap-max", type=float, default=DEFAULT_STOPBAND_GAP_MAX)
    parser.add_argument("--stopband-gap-step", type=float, default=DEFAULT_STOPBAND_GAP_STEP)
    parser.add_argument(
        "--stopband-gap-values",
        default=DEFAULT_STOPBAND_GAP_VALUES,
        help="stopband gap候補[Hz], カンマ区切り. 指定時はmin/max/stepより優先.",
    )
    parser.add_argument("--gpass-values", default=DEFAULT_GPASS_VALUES)
    parser.add_argument("--gstop-values", default=DEFAULT_GSTOP_VALUES)
    parser.add_argument("--acceptable-gain-db", type=float, default=DEFAULT_ACCEPTABLE_GAIN_DB)
    parser.add_argument(
        "--max-target-gain-db",
        type=float,
        default=DEFAULT_MAX_TARGET_GAIN_DB,
        help="target周波数で許容する最大ゲイン[dB]. 0以下で制限なし.",
    )
    parser.add_argument(
        "--target-neighborhood-width",
        type=float,
        default=DEFAULT_TARGET_NEIGHBORHOOD_WIDTH,
        help="target周波数周辺ゲインを確認する半幅[Hz]. 0ならtarget一点のみ.",
    )
    parser.add_argument(
        "--max-near-target-gain-db",
        type=float,
        default=DEFAULT_MAX_NEAR_TARGET_GAIN_DB,
        help="target±target-neighborhood-width内で許容する最大ゲイン[dB]. 0以下で制限なし.",
    )
    parser.add_argument("--max-q", type=float, default=DEFAULT_MAX_Q)
    parser.add_argument(
        "--max-target-delay-ms",
        "--target-delay-ms",
        type=float,
        default=DEFAULT_MAX_TARGET_DELAY_MS,
        help="target周波数と周辺で許容する最大群遅延[ms]. 1なら1ms以下. 0以下で制限なし.",
    )
    parser.add_argument(
        "--target-delay-neighborhood-width",
        "--delay-neighborhood-width",
        type=float,
        default=DEFAULT_TARGET_DELAY_NEIGHBORHOOD_WIDTH,
        help="群遅延上限を確認するtarget周波数周辺の半幅[Hz]. 0ならtarget一点のみ.",
    )
    parser.add_argument(
        "--target-delay-neighborhood-points",
        "--delay-neighborhood-points",
        type=int,
        default=DEFAULT_TARGET_DELAY_NEIGHBORHOOD_POINTS,
        help="target周波数周辺の群遅延を走査する点数.",
    )
    parser.add_argument("--max-pole-abs", type=float, default=DEFAULT_MAX_POLE_ABS)
    parser.add_argument(
        "--max-direct-form-order",
        type=int,
        default=DEFAULT_MAX_DIRECT_FORM_ORDER,
        help="a,b直接形で許容する最大次数. 0以下で制限なし.",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--wor-n", type=int, default=DEFAULT_WOR_N)
    parser.add_argument("--plot-max-freq", type=float, default=DEFAULT_PLOT_MAX_FREQ)
    parser.add_argument("--output", type=Path, help="候補と係数をJSON保存するパス.")
    parser.add_argument("--save-figure", help="周波数特性グラフを画像保存するパス.")
    parser.add_argument("--no-plot", action="store_true", help="グラフ表示を行わない.")
    parser.add_argument("--no-progress", dest="progress", action="store_false", help="進捗表示を行わない.")
    parser.add_argument("--interactive", action="store_true", help="対話入力で設定する. 指定しない場合はコマンド引数と既定値だけで実行.")
    parser.add_argument(
        "--no-check-filter",
        action="store_true",
        default=not DEFAULT_AUTO_CHECK_FILTER,
        help="探索後のcheck_filter.py自動実行を行わない.",
    )
    parser.add_argument(
        "--check-ranks",
        default=DEFAULT_CHECK_RANKS,
        help="自動チェックするRank. all, 1, 1-10, 1,3,5 など.",
    )
    parser.add_argument(
        "--detail-ranks",
        default=DEFAULT_DETAIL_RANKS,
        help="1枚まとめ表示で詳細確認するRank. all, 1, 1-10, 1,3,5 など.",
    )
    parser.add_argument(
        "--detail-rank",
        type=int,
        help="旧指定用. 指定すると --detail-ranks と同じ意味で単一Rankを表示.",
    )
    parser.add_argument(
        "--separate-check-plots",
        action="store_true",
        help="まとめ表示後にもcheck_filter.pyの詳細図を別ウィンドウで表示する.",
    )
    parser.add_argument(
        "--check-test-freqs",
        help="自動チェック用テスト周波数. 例: 10,7,20. 省略時はtargetのみ.",
    )
    parser.add_argument(
        "--check-test-amps",
        help="自動チェック用テスト振幅. 例: 1,0.5,0.5. 省略時は1.",
    )
    parser.add_argument("--check-save-figure", help="自動チェック図の保存先.")
    parser.add_argument("--check-save-info", help="自動チェック情報テキストの保存先.")
    parser.add_argument("--check-no-show", action="store_true", help="自動チェック図を表示しない.")
    parser.set_defaults(progress=True)
    return parser


def validate_args(args: argparse.Namespace):
    if args.samplerate <= 0:
        raise ValueError("samplerate は正の値")
    if args.target_freq <= 0:
        raise ValueError("target_freq は正の値")
    if args.passband_search_width <= 0:
        raise ValueError("passband_search_width は正の値")
    if args.passband_edge_step <= 0:
        raise ValueError("passband_edge_step は正の値")
    for value in parse_optional_float_list(args.passband_offset_values) or []:
        if value <= 0:
            raise ValueError("passband_offset_values は正の値")
    for value in parse_optional_float_list(args.passband_search_width_values) or []:
        if value <= 0:
            raise ValueError("passband_search_width_values は正の値")
    for value in parse_optional_float_list(args.passband_edge_step_values) or []:
        if value <= 0:
            raise ValueError("passband_edge_step_values は正の値")
    if args.stopband_gap_min <= 0 or args.stopband_gap_max <= 0:
        raise ValueError("stopband_gap は正の値")
    if args.stopband_gap_min > args.stopband_gap_max:
        raise ValueError("stopband_gap_min は stopband_gap_max 以下")
    if args.stopband_gap_step <= 0:
        raise ValueError("stopband_gap_step は正の値")
    for value in parse_optional_float_list(args.stopband_gap_values) or []:
        if value <= 0:
            raise ValueError("stopband_gap_values は正の値")
    build_families(args)
    if args.top_n <= 0:
        raise ValueError("top_n は正の整数")
    if not 0 < args.max_pole_abs <= 1:
        raise ValueError("max_pole_abs は 0より大きく1以下")
    if args.max_target_gain_db is not None and args.max_target_gain_db <= 0:
        args.max_target_gain_db = None
    if args.target_neighborhood_width < 0:
        raise ValueError("target_neighborhood_width は0以上")
    if args.max_near_target_gain_db is not None and args.max_near_target_gain_db <= 0:
        args.max_near_target_gain_db = None
    if args.max_target_delay_ms is not None and args.max_target_delay_ms <= 0:
        args.max_target_delay_ms = None
    if args.target_delay_neighborhood_width < 0:
        raise ValueError("target_delay_neighborhood_width は0以上")
    if args.target_delay_neighborhood_points <= 0:
        raise ValueError("target_delay_neighborhood_points は正の整数")
    if args.max_direct_form_order <= 0:
        args.max_direct_form_order = None
    if args.detail_rank is not None:
        if args.detail_rank <= 0:
            raise ValueError("detail-rank は正の整数")
        args.detail_ranks = str(args.detail_rank)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        require_scipy_signal()
        apply_positional_args(args)
        apply_search_preset(args, raw_argv)
        if args.interactive:
            apply_interactive_inputs(args)
        validate_args(args)
        candidates = find_candidates(args)
        print_candidates(candidates, args.target_freq)
        if args.output:
            save_candidates(args.output, args, candidates)
            print(f"Saved: {args.output}")
        combined_shown = plot_combined_sheet(candidates, args)
        if not combined_shown:
            plot_candidates(candidates, args)
        if candidates:
            if args.separate_check_plots or args.no_plot:
                run_check_filters(candidates, args)
            elif args.check_save_info or args.check_save_figure:
                saved_plot_args = argparse.Namespace(**vars(args))
                saved_plot_args.check_no_show = True
                run_check_filters(candidates, saved_plot_args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
