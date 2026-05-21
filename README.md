# 26_EEG

MAX2/EEGの計測, 解析, フィルタ設計をまとめた作業用プロジェクト.

このREADMEはプロジェクト全体の入口. 詳細な説明は各フォルダのREADMEを参照.

## ファイル説明

| ファイル/フォルダ | 内容 |
| :--- | :--- |
| [README.md](README.md) | プロジェクト全体の流れ, よく使うコマンド |
| [environment.yml](environment.yml) | Conda環境定義 |
| `.gitignore` | Git管理から除外するファイル設定 |
| [measurement/](measurement/) | MAX2シリアル受信と点滅刺激表示 |
| [analysis/](analysis/) | FFT, ウェーブレット, 位相タイミング解析 |
| [filter_design/](filter_design/) | 刺激周波数を強調するIIRピークフィルタ設計 |
| [hardware/](hardware/) | MAX2, COMポート, 実験前チェック |

## 目次

- [全体の流れ](#全体の流れ)
- [環境構築](#環境構築)
- [フォルダ構成](#フォルダ構成)
- [計測](#計測)
- [計測出力](#計測出力)
- [解析](#解析)
- [フィルタ設計](#フィルタ設計)
- [同期と注意](#同期と注意)
- [トラブルシュート](#トラブルシュート)
- [よく使うコマンド](#よく使うコマンド)

## 全体の流れ

1. MAX2とPCを接続し, COMポートを確認.
2. `eeg-max2` 環境を有効化.
3. `measurement/offline_max2_parallel_measurement.py` で計測.
4. `measurement/measurement_data/...` に保存された計測データを確認.
5. `analysis/FFT.py` や `analysis/Wavelet.py` で全体の周波数傾向を確認.
6. `filter_design/design_peak_filter.py` で刺激周波数用のバンドパス係数を設計.
7. `analysis/PhaseTiming.py` に係数を貼り, フレームログに基づいて位相タイミングを確認.

## 環境構築

既存環境を作る場合.

```powershell
conda env create -f environment.yml
conda activate eeg-max2
```

`environment.yml` を更新した後.

```powershell
conda env update -f environment.yml --prune
conda activate eeg-max2
```

主要ライブラリの確認.

```powershell
python -c "import numpy, scipy, matplotlib, serial; print('ok')"
```

## フォルダ構成

| パス | 内容 |
| :--- | :--- |
| `measurement/offline_max2_parallel_measurement.py` | MAX2からのシリアル受信と点滅刺激表示 |
| `measurement/measurement_data/` | 計測結果の保存先 |
| `analysis/FFT.py` | チャンネル別FFTと波形表示 |
| `analysis/Wavelet.py` | チャンネル別ウェーブレット解析 |
| `analysis/PhaseTiming.py` | 刺激周期ごとの位相タイミング確認 |
| `filter_design/design_peak_filter.py` | 指定周波数を強調するIIRピークフィルタ探索 |
| `filter_design/check_filter.py` | a,b係数の周波数特性, 遅延, 極配置確認 |
| `hardware/` | ハードウェア関連メモ |

## 計測

COMポート一覧を表示.

```powershell
python measurement\offline_max2_parallel_measurement.py --list-ports
```

COMポート番号を選んで計測.

```powershell
python measurement\offline_max2_parallel_measurement.py --com 1
```

COM名を直接指定して計測.

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM3
```

よく変更する設定.

```powershell
python measurement\offline_max2_parallel_measurement.py --com 1 --frequency 10 --pre-sec 10 --stim-sec 3 --post-sec 10
```

主なオプション.

| オプション | 内容 |
| :--- | :--- |
| `--list-ports` | 使用可能なCOMポート一覧を表示 |
| `--com` | COM番号またはCOM名を指定 |
| `--baud` | シリアル通信速度 |
| `--frequency` | 点滅刺激周波数[Hz] |
| `--pre-sec` | 刺激前の注視時間[s] |
| `--stim-sec` | 点滅刺激時間[s] |
| `--stim-cycles` | 点滅刺激を繰り返す回数 |
| `--post-sec` | 刺激後の注視時間[s] |
| `--stimulus-start` | `on` または `off` |
| `--fixation-color` | 注視点と非刺激時の灰色指定 |
| `--serial-warmup-sec` | シリアル接続直後に捨てる安定化時間[s] |
| `--windowed` | フルスクリーンではなくウィンドウ表示 |
| `--output-dir` | 保存先フォルダ |

初期値はコード冒頭の `DEFAULT_...` にまとめてある. 実験条件を固定したい場合は, まずそこを確認.

## 計測出力

計測すると次のようなフォルダが作成される.

```text
measurement/measurement_data/max2_parallel_YYYYMMDD_HHMMSS/
```

主な出力ファイル.

| ファイル | 内容 |
| :--- | :--- |
| `metadata.json` | 実験条件, COM設定, 周波数, 秒数など |
| `events.csv` | フェイズ開始, 終了などのイベント |
| `frames.csv` | フレームごとの表示状態と時刻 |
| `serial_samples.csv` | MAX2から受信したサンプル |
| `max2_summary.csv` | 計測概要 |

`frames.csv` は点滅刺激の同期確認で重要. 表示フレームごとのON/OFF状態を解析側で参照する.

## 解析

データフォルダ名だけで指定する場合.

```powershell
python analysis\FFT.py max2_parallel_20260520_185539
python analysis\Wavelet.py max2_parallel_20260520_185539
python analysis\PhaseTiming.py max2_parallel_20260520_185539
```

フルパスで指定する場合.

```powershell
python analysis\FFT.py C:\Users\g2110\Documents\EEG\26_EEG\measurement\measurement_data\max2_parallel_20260520_185539
```

解析コードの役割.

| ファイル | 目的 |
| :--- | :--- |
| `FFT.py` | チャンネルごとの波形と周波数成分を確認 |
| `Wavelet.py` | 時間ごとの周波数変化を確認 |
| `PhaseTiming.py` | 点滅周期ごとの波形を重ね, 位相の流れを確認 |

`PhaseTiming.py` は刺激フェイズについて `frames.csv` の実フレーム時刻を使う. そのため, 理想周期だけで切るよりもフレームずれを反映しやすい.

## フィルタ設計

10Hzを強調するピークフィルタを探索.

```powershell
python filter_design\design_peak_filter.py 10
```

対話なしで条件を指定.

```powershell
python filter_design\design_peak_filter.py 10 --samplerate 1000 --family butter --top-n 10
```

保存しながら実行.

```powershell
python filter_design\design_peak_filter.py 10 --save-json filter_design\peak_10hz.json --save-fig filter_design\peak_10hz.png
```

保存した係数を確認.

```powershell
python filter_design\check_filter.py --json filter_design\peak_10hz.json
```

フィルタ設計画面では, 左側の `R1`, `R2` などのチェックを切り替えることでランキング線を表示, 非表示にできる.

`design_peak_filter.py` の結果に表示される `DEFAULT_BANDPASS_A`, `DEFAULT_BANDPASS_B` を `analysis/PhaseTiming.py` に貼ると, 位相タイミング解析の前処理フィルタとして使える.

## 同期と注意

このプロジェクトでは, 計測データと刺激表示の対応を見るために複数の時刻を扱う.

| 種類 | 内容 |
| :--- | :--- |
| シリアル時刻 | MAX2から受信したサンプルのPC側受信時刻 |
| フレーム時刻 | 刺激表示フレームのPC側時刻 |
| フェイズ時刻 | 注視, 刺激, 注視などの区間開始時刻 |

`PhaseTiming.py` では, 刺激フェイズのON/OFF切り替わりを `frames.csv` から読む. ただし, MAX2と画面表示がハードウェア同期しているわけではないため, 厳密な同期が必要な実験ではフォトセンサやトリガ信号での確認が必要.

IIRバンドパスは周波数を鋭く強調できるが, 遅延と過渡応答が出る. 位相タイミング解析ではフィルタ遅延を意識し, 必ず `check_filter.py` で遅延, 極配置, 周波数特性を確認する.

## トラブルシュート

| 症状 | 確認すること |
| :--- | :--- |
| COMポートが見つからない | USB接続, デバイスマネージャ, `--list-ports` |
| `scipy` が見つからない | `conda env update -f environment.yml --prune` |
| グラフが表示されない | Matplotlibバックエンド, 実行環境, `--no-show` 指定の有無 |
| フィルタ候補が出ない | `--max-q`, `--max-delay-ms`, `--max-pole-abs`, 探索幅 |
| 解析の横軸が短い | 入力データフォルダ, `serial_samples.csv`, サンプリング周波数推定 |

## よく使うコマンド

```powershell
conda activate eeg-max2
python measurement\offline_max2_parallel_measurement.py --list-ports
python measurement\offline_max2_parallel_measurement.py --com 1
python analysis\FFT.py max2_parallel_20260520_185539
python analysis\Wavelet.py max2_parallel_20260520_185539
python filter_design\design_peak_filter.py 10
python filter_design\check_filter.py --json filter_design\peak_10hz.json
python analysis\PhaseTiming.py max2_parallel_20260520_185539
```
