#!/usr/bin/env python
"""
作成したデジタルフィルタの検証用グラフを一括表示するスクリプト.

表示内容:
  1. 周波数特性 Gain[dB] + 右軸の振幅倍率
  2. 群遅延 Delay[ms]
  3. 極配置・零点配置 z 平面
  4. 時間波形 入力 x[n] とフィルタ出力 y[n]

詳細な数値情報はグラフ上に重ねず, コンソールへ出力する.

使用ライブラリ:
  numpy, scipy, matplotlib

使い方例:
  # 1) design_peak_filter.py の --output で保存した JSON から Rank 1 を読む
  python check_filter_plots.py --filter-json filter_design/filter_10hz.json --rank 1

  # 2) PhaseTiming.py などの Python ファイルから係数定数を読む
  python check_filter_plots.py --module PhaseTiming.py

  # 3) 係数を直接指定する
  python check_filter_plots.py --b "0.1,0.0,-0.1" --a "1.0,-1.8,0.9"

  # 4) 混合波形で時間応答を確認する
  python check_filter_plots.py --filter-json filter_design/filter_10hz.json --test-freqs 10,7,20 --test-amps 1,0.5,0.5
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy import signal
import matplotlib.pyplot as plt


DEFAULT_SAMPLERATE = 1000.0
DEFAULT_TARGET_FREQ = 10.0
DEFAULT_WOR_N = 16000
DEFAULT_PLOT_MAX_FREQ = 60.0


class CoefficientLoadError(RuntimeError):
    pass


FLOAT_PATTERN = re.compile(
    r"[-+]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][-+]?\d+)?"
)


def parse_float_list(text: str) -> list[float]:
    """カンマ区切り, 空白区切り, 複数行貼り付けの数値列を list[float] に変換する."""
    if text is None:
        return []
    values = FLOAT_PATTERN.findall(text)
    if not values:
        raise argparse.ArgumentTypeError("数値を1つ以上指定してください")
    return [float(value) for value in values]


def ask_text(label: str, default):
    """Enterのみなら既定値を返す1行入力."""
    prompt = f"{label} [{default}]: " if default is not None else f"{label}: "
    value = input(prompt).strip()
    return default if value == "" else value


def ask_float(label: str, default: float) -> float:
    """数値を対話入力で受け取る."""
    while True:
        value = ask_text(label, default)
        try:
            return float(value)
        except ValueError:
            print("数値で入力してください.")


def ask_multiline_coefficients(label: str) -> np.ndarray:
    """係数を複数行で受け取る. 空行で入力終了."""
    print(label)
    print("  係数をカンマ区切り, 空白区切り, または複数行で貼り付けてください.")
    print("  入力が終わったら空行で確定します.")

    lines: list[str] = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)

    values = parse_float_list("\n".join(lines))
    return np.asarray(values, dtype=float)


def apply_interactive_inputs(args: argparse.Namespace):
    """引数なし実行時に必要な係数と解析条件を聞く."""
    print("係数が指定されていないため, 対話入力でフィルタを確認します.")
    args.a_values = ask_multiline_coefficients("a の値はいくつですか?")
    args.b_values = ask_multiline_coefficients("b の値はいくつですか?")

    args.samplerate = ask_float("サンプリング周波数 [Hz]", args.samplerate)
    args.target_freq = ask_float("確認したい中心周波数 [Hz]", args.target_freq)
    args.plot_max_freq = ask_float("グラフ表示の最大周波数 [Hz]", args.plot_max_freq)
    args.test_freqs = ask_text("時間波形の入力周波数 [Hz], カンマ区切り", str(args.target_freq))
    args.test_amps = ask_text("各入力周波数の振幅, カンマ区切り", "1")


def db_to_amplitude_ratio(db_values):
    """dB を振幅倍率に変換する. 振幅倍率 = 10^(dB/20)."""
    return np.power(10.0, np.asarray(db_values, dtype=float) / 20.0)


def format_amplitude_ratio(value: float) -> str:
    """右軸用の振幅倍率ラベルを作る."""
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
    """右軸を左軸の dB 目盛りに同期し, ラベルだけ振幅倍率にする."""
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


def load_coefficients_from_json(path: Path, rank: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """design_peak_filter.py の JSON 出力, または {a,b} を含む JSON から係数を読む."""
    payload = json.loads(path.read_text(encoding="utf-8"))

    metadata = {"source": str(path)}

    if isinstance(payload, dict) and "candidates" in payload:
        candidates = payload.get("candidates") or []
        if not candidates:
            raise CoefficientLoadError(f"JSON内に candidates がありません: {path}")
        if rank < 1 or rank > len(candidates):
            raise CoefficientLoadError(f"rank は 1〜{len(candidates)} の範囲で指定してください")
        selected = candidates[rank - 1]
        metadata.update(
            {
                "rank": rank,
                "family": selected.get("family"),
                "prototype_order": selected.get("prototype_order"),
                "direct_form_order": selected.get("direct_form_order"),
                "fp": selected.get("fp"),
                "fs": selected.get("fs"),
                "q_value": selected.get("q_value"),
                "gain_at_target_db": selected.get("gain_at_target_db"),
            }
        )
        return np.asarray(selected["b"], dtype=float), np.asarray(selected["a"], dtype=float), metadata

    if isinstance(payload, dict) and "a" in payload and "b" in payload:
        return np.asarray(payload["b"], dtype=float), np.asarray(payload["a"], dtype=float), metadata

    raise CoefficientLoadError(
        "JSON形式が未対応です. design_peak_filter.py の出力, または {'a': [...], 'b': [...]} を指定してください."
    )


def load_coefficients_from_module(
    path: Path,
    a_name: str = "DEFAULT_BANDPASS_A",
    b_name: str = "DEFAULT_BANDPASS_B",
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Python ファイルから DEFAULT_BANDPASS_A / DEFAULT_BANDPASS_B などの定数を読む."""
    module_name = "_filter_coefficients_module"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CoefficientLoadError(f"Pythonファイルを読み込めません: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, a_name):
        raise CoefficientLoadError(f"{path} に {a_name} が見つかりません")
    if not hasattr(module, b_name):
        raise CoefficientLoadError(f"{path} に {b_name} が見つかりません")

    a = np.asarray(getattr(module, a_name), dtype=float)
    b = np.asarray(getattr(module, b_name), dtype=float)
    return b, a, {"source": str(path), "a_name": a_name, "b_name": b_name}


def load_coefficients(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, dict]:
    """コマンドライン引数または対話入力からフィルタ係数 b, a を取得する."""
    if getattr(args, "a_values", None) is not None and getattr(args, "b_values", None) is not None:
        return args.b_values, args.a_values, {"source": "interactive input"}

    if args.filter_json:
        return load_coefficients_from_json(Path(args.filter_json), args.rank)

    if args.module:
        return load_coefficients_from_module(Path(args.module), args.a_name, args.b_name)

    if args.a and args.b:
        a = np.asarray(parse_float_list(args.a), dtype=float)
        b = np.asarray(parse_float_list(args.b), dtype=float)
        return b, a, {"source": "command line"}

    raise CoefficientLoadError(
        "係数の指定がありません. --filter-json, --module, または --a と --b を指定してください."
    )


def validate_coefficients(b: np.ndarray, a: np.ndarray):
    if b.ndim != 1 or a.ndim != 1:
        raise ValueError("a, b は1次元配列である必要があります")
    if len(a) == 0 or len(b) == 0:
        raise ValueError("a, b は空にできません")
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        raise ValueError("a, b に NaN または Inf が含まれています")
    if a[0] == 0:
        raise ValueError("a[0] は 0 にできません")


def normalize_coefficients(b: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """a[0] が 1 でない場合は正規化する."""
    if a[0] == 1.0:
        return b, a
    return b / a[0], a / a[0]


def compute_frequency_response(
    b: np.ndarray,
    a: np.ndarray,
    samplerate: float,
    wor_n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    w, h = signal.freqz(b, a, worN=wor_n)
    frequencies = w / (2.0 * np.pi) * samplerate
    magnitude = np.maximum(np.abs(h), 1e-20)
    response_db = 20.0 * np.log10(magnitude)
    return frequencies, response_db, h


def compute_group_delay(
    b: np.ndarray,
    a: np.ndarray,
    samplerate: float,
    wor_n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """群遅延を samples と ms で返す."""
    w = np.linspace(0.0, np.pi, wor_n, endpoint=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w_delay, delay_samples = signal.group_delay((b, a), w=w)
    delay_freq = w_delay / (2.0 * np.pi) * samplerate
    delay_ms = delay_samples / samplerate * 1000.0
    return delay_freq, delay_samples, delay_ms


def compute_poles_zeros(b: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, bool]:
    """零点, 極, 最大極半径, 安定判定を返す."""
    zeros = np.roots(b) if len(b) > 1 else np.asarray([], dtype=complex)
    poles = np.roots(a) if len(a) > 1 else np.asarray([], dtype=complex)
    max_pole_abs = float(np.max(np.abs(poles))) if poles.size else 0.0
    is_stable = bool(max_pole_abs < 1.0)
    return zeros, poles, max_pole_abs, is_stable


def pole_frequency_hz(pole: complex, samplerate: float) -> float:
    """極の角度を対応する周波数[Hz]に変換する."""
    return float(np.angle(pole) / (2.0 * np.pi) * samplerate)


def format_complex(value: complex) -> str:
    """複素数を短く表示する."""
    sign = "+" if value.imag >= 0 else "-"
    return f"{value.real:.6f} {sign} {abs(value.imag):.6f}j"


def describe_poles(poles: np.ndarray, samplerate: float, max_count: int = 8) -> list[str]:
    """代表的な極をテキスト化する."""
    if poles.size == 0:
        return ["poles: none"]

    # 半径が大きい順に表示. IIRでは単位円に近い極が応答を支配しやすい.
    order = np.argsort(np.abs(poles))[::-1]
    lines = ["poles:"]
    for index in order[:max_count]:
        pole = poles[index]
        radius = abs(pole)
        freq_hz = pole_frequency_hz(pole, samplerate)
        lines.append(f"  {format_complex(pole)}, |p|={radius:.6f}, angle_freq={freq_hz:.3f} Hz")
    if len(order) > max_count:
        lines.append(f"  ... {len(order) - max_count} more")
    return lines


def build_test_signal(
    samplerate: float,
    duration_sec: float,
    test_freqs: list[float],
    test_amps: list[float],
) -> tuple[np.ndarray, np.ndarray]:
    """複数正弦波の合成入力信号を作る."""
    sample_count = int(round(duration_sec * samplerate))
    if sample_count <= 1:
        raise ValueError("duration_sec が短すぎます")

    time_sec = np.arange(sample_count, dtype=float) / samplerate
    x = np.zeros_like(time_sec)
    for freq, amp in zip(test_freqs, test_amps):
        x += amp * np.sin(2.0 * np.pi * freq * time_sec)
    return time_sec, x


def nearest_value(x_values: np.ndarray, y_values: np.ndarray, x: float) -> float:
    index = int(np.argmin(np.abs(x_values - x)))
    return float(y_values[index])


def format_metadata(metadata: dict) -> str:
    parts = []
    if metadata.get("source"):
        parts.append(f"source={metadata['source']}")
    if metadata.get("rank"):
        parts.append(f"rank={metadata['rank']}")
    if metadata.get("family"):
        parts.append(f"family={metadata['family']}")
    if metadata.get("direct_form_order") is not None:
        parts.append(f"order={metadata['direct_form_order']}")
    if metadata.get("q_value") is not None:
        parts.append(f"Q={metadata['q_value']:.2f}")
    return ", ".join(parts)


def make_summary_text(
    frequencies: np.ndarray,
    response_db: np.ndarray,
    delay_freq: np.ndarray,
    delay_ms: np.ndarray,
    target_freq: float,
    test_freqs: list[float],
    poles: np.ndarray,
    max_pole_abs: float,
    is_stable: bool,
    samplerate: float,
) -> str:
    gain_target_db = nearest_value(frequencies, response_db, target_freq)
    ratio_target = float(db_to_amplitude_ratio(gain_target_db))
    delay_target_ms = nearest_value(delay_freq, delay_ms, target_freq)

    lines = [
        f"target {target_freq:g} Hz: gain={gain_target_db:.2f} dB ({ratio_target:.3f}x), "
        f"group delay={delay_target_ms:.2f} ms",
        f"max |pole|={max_pole_abs:.8f}, stable={'yes' if is_stable else 'no'}",
    ]

    for freq in test_freqs:
        if math.isclose(freq, target_freq, rel_tol=0.0, abs_tol=1e-12):
            continue
        gain_db = nearest_value(frequencies, response_db, freq)
        ratio = float(db_to_amplitude_ratio(gain_db))
        lines.append(f"test {freq:g} Hz: gain={gain_db:.2f} dB ({ratio:.3f}x)")

    lines.extend(describe_poles(poles, samplerate, max_count=6))
    return "\n".join(lines)


def plot_all(
    b: np.ndarray,
    a: np.ndarray,
    args: argparse.Namespace,
    metadata: dict,
):
    frequencies, response_db, _ = compute_frequency_response(b, a, args.samplerate, args.wor_n)
    delay_freq, delay_samples, delay_ms = compute_group_delay(b, a, args.samplerate, args.wor_n)
    zeros, poles, max_pole_abs, is_stable = compute_poles_zeros(b, a)

    test_freqs = parse_float_list(args.test_freqs) if args.test_freqs else [args.target_freq]
    test_amps = parse_float_list(args.test_amps) if args.test_amps else [1.0] * len(test_freqs)
    if len(test_amps) != len(test_freqs):
        raise ValueError("--test-freqs と --test-amps の個数を一致させてください")

    duration_sec = args.duration_ms / 1000.0
    time_sec, x = build_test_signal(args.samplerate, duration_sec, test_freqs, test_amps)
    y = signal.lfilter(b, a, x)

    plot_start_sec = args.settle_ms / 1000.0
    plot_end_sec = min(duration_sec, plot_start_sec + args.plot_window_ms / 1000.0)
    mask_time = (time_sec >= plot_start_sec) & (time_sec <= plot_end_sec)
    if not np.any(mask_time):
        raise ValueError("表示する時間範囲が空です. --duration-ms, --settle-ms, --plot-window-ms を確認してください")

    mask_freq = frequencies <= args.plot_max_freq
    mask_delay = (delay_freq <= args.plot_max_freq) & np.isfinite(delay_ms) & np.isfinite(delay_samples)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)
    ax_response = axes[0, 0]
    ax_delay = axes[1, 0]
    ax_zplane = axes[0, 1]
    ax_time = axes[1, 1]

    fig.suptitle("Filter check: frequency response / group delay / z-plane / time waveform")

    # 1. 周波数特性
    ax_response.plot(frequencies[mask_freq], response_db[mask_freq], label="Gain")
    ax_response.axvline(args.target_freq, linestyle="--", linewidth=1.0, label="target")
    ax_response.axhline(-3.0, linestyle=":", linewidth=1.0, label="-3 dB")
    ax_response.set_title("Frequency response")
    ax_response.set_xlabel("Frequency [Hz]")
    ax_response.set_ylabel("Gain [dB]")
    ax_response.set_xlim(0.0, args.plot_max_freq)
    ax_response.grid(True)
    ax_response.legend(loc="best")

    ratio_axis = ax_response.twinx()
    ratio_axis.set_ylabel("Amplitude ratio")
    sync_ratio_axis_to_db_axis(ax_response, ratio_axis)
    ax_response.callbacks.connect(
        "ylim_changed",
        lambda axis: sync_ratio_axis_to_db_axis(axis, ratio_axis),
    )

    # 2. 群遅延
    ax_delay.plot(delay_freq[mask_delay], delay_ms[mask_delay], label="Group delay")
    ax_delay.axvline(args.target_freq, linestyle="--", linewidth=1.0, label="target")
    ax_delay.set_title("Group delay")
    ax_delay.set_xlabel("Frequency [Hz]")
    ax_delay.set_ylabel("Delay [ms]")
    ax_delay.set_xlim(0.0, args.plot_max_freq)
    if args.delay_ylim is not None:
        ymin, ymax = parse_float_list(args.delay_ylim)
        ax_delay.set_ylim(ymin, ymax)
    ax_delay.grid(True)
    ax_delay.legend(loc="best")

    # 3. 極配置・零点配置 z 平面
    theta = np.linspace(0.0, 2.0 * np.pi, 720)
    ax_zplane.plot(np.cos(theta), np.sin(theta), linestyle="--", linewidth=1.0, label="Unit circle")
    ax_zplane.axhline(0.0, linewidth=0.8)
    ax_zplane.axvline(0.0, linewidth=0.8)
    if zeros.size:
        ax_zplane.scatter(np.real(zeros), np.imag(zeros), marker="o", facecolors="none", label="Zeros")
    if poles.size:
        ax_zplane.scatter(np.real(poles), np.imag(poles), marker="x", label="Poles")
    limit = 1.1
    if zeros.size or poles.size:
        all_points = np.concatenate([zeros, poles]) if zeros.size and poles.size else (zeros if zeros.size else poles)
        max_abs = float(np.max(np.abs(all_points)))
        limit = max(1.1, math.ceil(max_abs * 10.0) / 10.0 + 0.1)
    ax_zplane.set_title(f"Pole-zero plot (max |pole|={max_pole_abs:.6f}, stable={'yes' if is_stable else 'no'})")
    ax_zplane.set_xlabel("Real")
    ax_zplane.set_ylabel("Imaginary")
    ax_zplane.set_xlim(-limit, limit)
    ax_zplane.set_ylim(-limit, limit)
    ax_zplane.set_aspect("equal", adjustable="box")
    ax_zplane.grid(True)
    ax_zplane.legend(loc="best")

    # 4. 時間波形
    ax_time.plot(time_sec[mask_time] * 1000.0, x[mask_time], label="Input")
    ax_time.plot(time_sec[mask_time] * 1000.0, y[mask_time], label="Output (lfilter, causal)")
    ax_time.set_title("Time waveform")
    ax_time.set_xlabel("Time [ms]")
    ax_time.set_ylabel("Amplitude")
    ax_time.grid(True)
    ax_time.legend(loc="best")

    summary = make_summary_text(
        frequencies,
        response_db,
        delay_freq,
        delay_ms,
        args.target_freq,
        test_freqs,
        poles,
        max_pole_abs,
        is_stable,
        args.samplerate,
    )
    metadata_text = format_metadata(metadata)
    info_text = summary if not metadata_text else metadata_text + "\n" + summary

    # 文字情報を図の中に重ねるとグラフを隠してしまうため, 図中には表示しない.
    # 詳細情報はコンソールに出力する. 必要なら --save-info でテキスト保存できる.
    if args.save_info:
        info_path = Path(args.save_info)
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(info_text + "\n", encoding="utf-8")
        print(f"Info saved: {info_path}")

    if args.save_figure:
        save_path = Path(args.save_figure)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=args.dpi)
        print(f"Figure saved: {save_path}")

    print(info_text)
    plt.show()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="作成したデジタルフィルタの周波数特性・群遅延・極配置・時間波形を一括表示する."
    )

    source = parser.add_argument_group("係数の読み込み")
    source.add_argument("--filter-json", help="design_peak_filter.py の --output で保存したJSON")
    source.add_argument("--rank", type=int, default=1, help="JSON内の候補Rank. 1始まり")
    source.add_argument("--module", help="DEFAULT_BANDPASS_A/B などを含むPythonファイル")
    source.add_argument("--a-name", default="DEFAULT_BANDPASS_A", help="Pythonファイル内のa係数名")
    source.add_argument("--b-name", default="DEFAULT_BANDPASS_B", help="Pythonファイル内のb係数名")
    source.add_argument("--a", help="a係数. 例: '1.0,-1.8,0.9'")
    source.add_argument("--b", help="b係数. 例: '0.1,0.0,-0.1'")

    setting = parser.add_argument_group("解析設定")
    setting.add_argument("--samplerate", type=float, default=DEFAULT_SAMPLERATE, help="サンプリング周波数[Hz]")
    setting.add_argument("--target-freq", type=float, default=DEFAULT_TARGET_FREQ, help="確認したい中心周波数[Hz]")
    setting.add_argument("--wor-n", type=int, default=DEFAULT_WOR_N, help="周波数応答の計算点数")
    setting.add_argument("--plot-max-freq", type=float, default=DEFAULT_PLOT_MAX_FREQ, help="横軸に表示する最大周波数[Hz]")
    setting.add_argument("--delay-ylim", help="群遅延の縦軸範囲. 例: '0,500'")

    time_setting = parser.add_argument_group("時間波形設定")
    time_setting.add_argument("--test-freqs", help="時間波形の入力周波数[Hz]. 例: '10,7,20'. 未指定なら target-freq のみ")
    time_setting.add_argument("--test-amps", help="各入力周波数の振幅. 例: '1,0.5,0.5'. 未指定なら全て1")
    time_setting.add_argument("--duration-ms", type=float, default=2000.0, help="シミュレーション時間[ms]")
    time_setting.add_argument("--settle-ms", type=float, default=1000.0, help="過渡応答を避けるため表示を開始する時刻[ms]")
    time_setting.add_argument("--plot-window-ms", type=float, default=500.0, help="時間波形として表示する長さ[ms]")

    output = parser.add_argument_group("出力")
    output.add_argument("--save-figure", help="グラフ画像の保存先. 例: filter_check.png")
    output.add_argument("--save-info", help="判定結果や極情報のテキスト保存先. 例: filter_check_info.txt")
    output.add_argument("--dpi", type=int, default=150, help="保存画像のDPI")

    return parser


def validate_args(args: argparse.Namespace):
    if args.samplerate <= 0:
        raise ValueError("samplerate は正の値にしてください")
    if not 0 < args.target_freq < args.samplerate / 2.0:
        raise ValueError("target-freq は 0Hzより大きく, ナイキスト周波数より小さくしてください")
    if args.wor_n <= 0:
        raise ValueError("wor-n は正の整数にしてください")
    if args.plot_max_freq <= 0:
        raise ValueError("plot-max-freq は正の値にしてください")
    if args.duration_ms <= 0 or args.plot_window_ms <= 0:
        raise ValueError("duration-ms と plot-window-ms は正の値にしてください")
    if args.settle_ms < 0:
        raise ValueError("settle-ms は0以上にしてください")
    if args.delay_ylim is not None and len(parse_float_list(args.delay_ylim)) != 2:
        raise ValueError("--delay-ylim は '最小,最大' の2値で指定してください")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if len(sys.argv) == 1:
            apply_interactive_inputs(args)
        validate_args(args)
        b, a, metadata = load_coefficients(args)
        validate_coefficients(b, a)
        b, a = normalize_coefficients(b, a)
        plot_all(b, a, args, metadata)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
