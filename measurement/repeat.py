<<<<<<< HEAD
# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


# ===== ここを変更して使う =====

# 実行する回数.
RUN_COUNT = 10

# 実行するコマンド.
# sys.executable は, このスクリプトを起動したPython/conda環境と同じPythonを使う指定.
# Windowsで文字列として見ると, ほぼ
# python measurement\offline_max2_parallel_measurement.py --com 2
# と同じ意味になる.
COMMAND = [
    sys.executable,
    str(Path("measurement") / "offline_max2_parallel_measurement.py"),
    "--com",
    "2",
]

# 測定コマンドを実行する作業フォルダ.
# このファイルは measurement フォルダ内に置くため, 1つ上の 26_EEG を基準にする.
WORKING_DIRECTORY = Path(__file__).resolve().parents[1]

# 各回の間に待つ秒数. 不要なら 0.0.
INTERVAL_SECONDS = 0.0

# True の場合, 途中でエラー終了したら繰り返しを止める.
STOP_ON_ERROR = True


def format_command(command: list[str]) -> str:
    return " ".join(f'"{item}"' if " " in item else item for item in command)


def main() -> int:
    if RUN_COUNT <= 0:
        raise ValueError("RUN_COUNT は1以上にしてください.")

    print(f"working directory: {WORKING_DIRECTORY}")
    print(f"command: {format_command(COMMAND)}")
    print(f"run count: {RUN_COUNT}")
    print()

    last_return_code = 0
    for run_index in range(1, RUN_COUNT + 1):
        print(f"===== run {run_index}/{RUN_COUNT} start =====")
        start_time = time.perf_counter()

        completed = subprocess.run(COMMAND, cwd=WORKING_DIRECTORY)
        last_return_code = int(completed.returncode)

        elapsed_sec = time.perf_counter() - start_time
        print(f"===== run {run_index}/{RUN_COUNT} end: returncode={last_return_code}, elapsed={elapsed_sec:.1f}s =====")
        print()

        if last_return_code != 0 and STOP_ON_ERROR:
            print("途中でエラー終了したため, 繰り返しを停止しました.")
            return last_return_code

        if run_index < RUN_COUNT and INTERVAL_SECONDS > 0:
            time.sleep(INTERVAL_SECONDS)

    return last_return_code
=======
"""Run the measurement CLI repeatedly, resolving interactive choices once."""
import argparse
import subprocess
import sys
from pathlib import Path

try:
    from .offline_max2_parallel_measurement import parse_args, resolve_com_port
    from .device_backends import resolve_device
except ImportError:
    from offline_max2_parallel_measurement import parse_args, resolve_com_port
    from device_backends import resolve_device

RUN_COUNT = 3
COMMAND = []  # Measurement options may also be configured here.


def main(argv=None):
    parser = argparse.ArgumentParser(description="Repeat measurement; remaining options pass to measurement.")
    parser.add_argument("--count", type=int, default=RUN_COUNT)
    args, options = parser.parse_known_args(argv)
    if args.count < 1:
        parser.error("--count must be positive")
    options = COMMAND + options
    config = parse_args(options)
    if config.list_ports:
        args.count = 1
    else:
        device = resolve_device(config.device)
        port = resolve_com_port(config.com)
        options += ["--device", device, "--com", port]
    script = Path(__file__).with_name("offline_max2_parallel_measurement.py")
    for index in range(args.count):
        print(f"Run {index + 1}/{args.count}", flush=True)
        result = subprocess.run([sys.executable, str(script), *options])
        if result.returncode:
            return result.returncode
    return 0
>>>>>>> 53e9d44 (更新)


if __name__ == "__main__":
    raise SystemExit(main())
