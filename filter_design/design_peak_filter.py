#!/usr/bin/env python
"""
狙った刺激周波数を強調するピークフィルタを探索, 設計するスクリプト.

例:
    python filter_design/design_peak_filter.py
    python filter_design/design_peak_filter.py --target-freq 10 --samplerate 1000
    python filter_design/design_peak_filter.py --target-freq 7.5 --output filter_design/filter_7_5hz.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


# サンプリング周波数, EEGの取得レート.
DEFAULT_SAMPLERATE = 1000.0

# 強調したい刺激周波数, SSVEPの基本周波数.
DEFAULT_TARGET_FREQ = 10.0

# 通過帯域候補の探索幅, target_freqを中心にこの幅の中で上下端を動かす.
DEFAULT_PASSBAND_SEARCH_WIDTH = 1.0

# 通過帯域端候補の刻み幅, 小さいほど細かく探すが時間がかかる.
DEFAULT_PASSBAND_EDGE_STEP = 0.05

# 阻止帯と通過帯の間隔の最小値, Hz.
DEFAULT_STOPBAND_GAP_MIN = 0.1

# 阻止帯と通過帯の間隔の最大値, Hz.
DEFAULT_STOPBAND_GAP_MAX = 4.0

# 阻止帯と通過帯の間隔の刻み幅, Hz.
DEFAULT_STOPBAND_GAP_STEP = 0.1

# 通過域端最大損失[dB], 小さいほど通過帯が平坦.
DEFAULT_GPASS_VALUES = "1"

# 阻止域端最小減衰[dB], 大きいほど強く止めるが次数が増えやすい.
DEFAULT_GSTOP_VALUES = "80,120,160,200"

# target_freqで許容する最小ゲイン[dB].
DEFAULT_ACCEPTABLE_GAIN_DB = -3.0

# Q値の上限, 高すぎる候補を避ける.
DEFAULT_MAX_Q = 400.0

# 極の絶対値の上限, 1未満なら理論上安定.
DEFAULT_MAX_POLE_ABS = 0.99999

# a,b直接形で許容する最大次数, 高すぎるIIRは数値誤差が出やすい.
DEFAULT_MAX_DIRECT_FORM_ORDER = 12

# 表示, 保存する候補数.
DEFAULT_TOP_N = 10

# 周波数応答の計算点数.
DEFAULT_WOR_N = 16000

# グラフで表示する最大周波数.
DEFAULT_PLOT_MAX_FREQ = 60.0


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
    w, h = signal.freqz(b, a, worN=wor_n)
    frequencies = w / (2.0 * np.pi) * samplerate
    magnitude = np.abs(h)
    magnitude[magnitude == 0] = 1e-20
    return frequencies, 20.0 * np.log10(magnitude)


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


def iter_candidate_edges(target_freq: float, search_width: float, edge_step: float):
    half_width = search_width / 2.0
    low_values = frange(max(0.01, target_freq - half_width), target_freq - edge_step, edge_step)
    high_values = frange(target_freq + edge_step, target_freq + half_width, edge_step)
    for fp0 in low_values:
        for fp1 in high_values:
            if fp0 < target_freq < fp1:
                yield float(fp0), float(fp1)


def find_candidates(args: argparse.Namespace) -> list[FilterCandidate]:
    signal = require_scipy_signal()

    samplerate = args.samplerate
    target_freq = args.target_freq
    nyquist = samplerate / 2.0
    if not 0.0 < target_freq < nyquist:
        raise ValueError("target_freq は 0Hzより大きく, ナイキスト周波数より小さい必要あり")

    pass_edges = list(iter_candidate_edges(target_freq, args.passband_search_width, args.passband_edge_step))
    stop_gaps = frange(args.stopband_gap_min, args.stopband_gap_max, args.stopband_gap_step)
    gpass_values = parse_float_list(args.gpass_values)
    gstop_values = parse_float_list(args.gstop_values)
    total = len(pass_edges) * len(stop_gaps) * len(gpass_values) * len(gstop_values)
    progress_unit = max(total // 100, 1)
    candidates: list[FilterCandidate] = []

    if total == 0:
        raise ValueError("探索範囲が空, passband_search_width と passband_edge_step を確認")

    step_count = 0
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
                        order, b, a = design_iir(signal, args.family, wp, ws, gpass, gstop)
                    except ValueError:
                        continue

                    direct_form_order = len(a) - 1
                    if (
                        args.max_direct_form_order is not None
                        and direct_form_order > args.max_direct_form_order
                    ):
                        continue

                    poles = np.roots(a)
                    max_pole_abs = float(np.max(np.abs(poles))) if poles.size else 0.0
                    if max_pole_abs >= args.max_pole_abs:
                        continue

                    frequencies, db_values = response_db(signal, b, a, samplerate, args.wor_n)
                    gain_at_target = float(np.interp(target_freq, frequencies, db_values))
                    if gain_at_target < args.acceptable_gain_db:
                        continue

                    bandwidth, q_value = calc_bandwidth_and_q(
                        frequencies, db_values, target_freq, gain_at_target
                    )
                    if bandwidth is None or q_value is None:
                        continue
                    if args.max_q is not None and q_value > args.max_q:
                        continue

                    candidates.append(
                        FilterCandidate(
                            family=args.family,
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
            -item.direct_form_order,
            item.gain_at_target_db,
        ),
        reverse=True,
    )
    return candidates[: args.top_n]


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
        print(f"  bandwidth_3db: {candidate.bandwidth_3db:.4f} Hz")
        print(f"  Q: {candidate.q_value:.2f}")
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
            "family": args.family,
            "passband_search_width": args.passband_search_width,
            "passband_edge_step": args.passband_edge_step,
            "stopband_gap_min": args.stopband_gap_min,
            "stopband_gap_max": args.stopband_gap_max,
            "stopband_gap_step": args.stopband_gap_step,
            "gpass_values": parse_float_list(args.gpass_values),
            "gstop_values": parse_float_list(args.gstop_values),
            "acceptable_gain_db": args.acceptable_gain_db,
            "max_q": args.max_q,
            "max_pole_abs": args.max_pole_abs,
            "max_direct_form_order": args.max_direct_form_order,
        },
        "candidates": [candidate.to_json_dict() for candidate in candidates],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def plot_candidates(candidates: list[FilterCandidate], args: argparse.Namespace):
    if not candidates or args.no_plot:
        return

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib が見つからないためグラフ表示をスキップ.")
        return

    signal = require_scipy_signal()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), constrained_layout=True)
    ax_response, ax_delay = axes

    for rank, candidate in enumerate(candidates, start=1):
        mask = candidate.frequencies <= args.plot_max_freq
        label = f"Rank {rank}, Q={candidate.q_value:.1f}, order={candidate.direct_form_order}"
        ax_response.plot(candidate.frequencies[mask], candidate.response_db[mask], label=label)

        try:
            w, delay_samples = signal.group_delay((candidate.b, candidate.a), w=args.wor_n)
            delay_freq = w / (2.0 * np.pi) * args.samplerate
            delay_ms = delay_samples / args.samplerate * 1000.0
            delay_mask = delay_freq <= args.plot_max_freq
            ax_delay.plot(delay_freq[delay_mask], delay_ms[delay_mask], label=f"Rank {rank}")
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

    if args.save_figure:
        save_path = Path(args.save_figure)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=150)
        print(f"Figure saved: {save_path}")

    plt.show()


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
    args.family = ask_choice("IIR設計法", ["butter", "cheby1", "cheby2", "ellip"], args.family)

    edit_detail = ask_bool("詳細設定を変更する", False)
    if edit_detail:
        args.passband_search_width = ask_float("通過帯域候補の探索幅[Hz]", args.passband_search_width)
        args.passband_edge_step = ask_float("通過帯域端候補の刻み幅[Hz]", args.passband_edge_step)
        args.stopband_gap_min = ask_float("阻止帯ギャップ最小[Hz]", args.stopband_gap_min)
        args.stopband_gap_max = ask_float("阻止帯ギャップ最大[Hz]", args.stopband_gap_max)
        args.stopband_gap_step = ask_float("阻止帯ギャップ刻み[Hz]", args.stopband_gap_step)
        args.gpass_values = ask_text("通過域端最大損失[dB], カンマ区切り", args.gpass_values)
        args.gstop_values = ask_text("阻止域端最小減衰[dB], カンマ区切り", args.gstop_values)
        args.acceptable_gain_db = ask_float("target周波数の許容最小ゲイン[dB]", args.acceptable_gain_db)
        args.max_q = ask_float("Q値上限, 0で制限なし", args.max_q, allow_none=True)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="狙った刺激周波数を強調するピークフィルタを探索, 設計する."
    )
    parser.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--target-freq", type=float, default=DEFAULT_TARGET_FREQ)
    parser.add_argument(
        "--family",
        choices=["butter", "cheby1", "cheby2", "ellip"],
        default="butter",
        help="IIR設計法.",
    )
    parser.add_argument("--passband-search-width", type=float, default=DEFAULT_PASSBAND_SEARCH_WIDTH)
    parser.add_argument("--passband-edge-step", type=float, default=DEFAULT_PASSBAND_EDGE_STEP)
    parser.add_argument("--stopband-gap-min", type=float, default=DEFAULT_STOPBAND_GAP_MIN)
    parser.add_argument("--stopband-gap-max", type=float, default=DEFAULT_STOPBAND_GAP_MAX)
    parser.add_argument("--stopband-gap-step", type=float, default=DEFAULT_STOPBAND_GAP_STEP)
    parser.add_argument("--gpass-values", default=DEFAULT_GPASS_VALUES)
    parser.add_argument("--gstop-values", default=DEFAULT_GSTOP_VALUES)
    parser.add_argument("--acceptable-gain-db", type=float, default=DEFAULT_ACCEPTABLE_GAIN_DB)
    parser.add_argument("--max-q", type=float, default=DEFAULT_MAX_Q)
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
    parser.add_argument("--interactive", action="store_true", help="対話入力で設定する.")
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
    if args.stopband_gap_min <= 0 or args.stopband_gap_max <= 0:
        raise ValueError("stopband_gap は正の値")
    if args.stopband_gap_min > args.stopband_gap_max:
        raise ValueError("stopband_gap_min は stopband_gap_max 以下")
    if args.stopband_gap_step <= 0:
        raise ValueError("stopband_gap_step は正の値")
    if args.top_n <= 0:
        raise ValueError("top_n は正の整数")
    if not 0 < args.max_pole_abs <= 1:
        raise ValueError("max_pole_abs は 0より大きく1以下")
    if args.max_direct_form_order <= 0:
        args.max_direct_form_order = None


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        require_scipy_signal()
        if args.interactive or len(raw_argv) == 0:
            apply_interactive_inputs(args)
        validate_args(args)
        candidates = find_candidates(args)
        print_candidates(candidates, args.target_freq)
        if args.output:
            save_candidates(args.output, args, candidates)
            print(f"Saved: {args.output}")
        plot_candidates(candidates, args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
