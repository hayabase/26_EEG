# 26_EEG README

MAX2/EEG計測, オフライン解析, 刺激周波数用IIRフィルタ設計をまとめたプロジェクト.

## フォルダ構成

| パス | 内容 |
|---|---|
| `measurement/offline_max2_parallel_measurement.py` | MAX2からシリアル受信しながら, 注視点と点滅刺激を表示して計測データを保存 |
| `analysis/FFT.py` | 計測データの時系列波形とFFTを表示 |
| `analysis/Wavelet.py` | 計測データの時間-周波数変化をウェーブレット変換で表示 |
| `analysis/PhaseTiming.py` | 刺激周期ごとに波形を折り重ね, 位相タイミングを確認 |
| `filter_design/design_peak_filter.py` | 刺激周波数だけを強調するIIRピーク/BPF候補を探索 |
| `filter_design/check_filter.py` | 作成済みフィルタ係数の周波数特性, 群遅延, 極配置, 時間応答を確認 |
| `hardware/hardware_README.md` | MAX2/EEGボード接続, COMポート, 実験前チェック |
| `analysis/analysis_README.md` | 解析スクリプトの詳細 |
| `environment.yml` | conda環境定義 |

## 環境構築

新規作成:

```powershell
conda env create -f environment.yml
conda activate eeg-max2
```

既存環境を更新:

```powershell
conda env update -f environment.yml --prune
conda activate eeg-max2
```

依存ライブラリ:

| 用途 | ライブラリ |
|---|---|
| 数値計算 | `numpy`, `scipy` |
| グラフ表示 | `matplotlib` |
| シリアル通信 | `pyserial` |
| OpenGL表示 | `glfw`, `pyglfw`, `pyopengl` |
| Windows優先度制御 | `psutil` |

インストール確認:

```powershell
python -c "import numpy, scipy, matplotlib, serial, glfw, OpenGL, psutil; print('ok')"
```

## 計測の流れ

1. MAX2/EEGボードをPCに接続.
2. COMポートを確認.
3. `offline_max2_parallel_measurement.py` を実行.
4. `measurement/measurement_data/max2_parallel_YYYYMMDD_HHMMSS/` にCSVとJSONが保存.
5. `analysis` のスクリプトでFFT, ウェーブレット, 位相タイミングを確認.

COMポート一覧:

```powershell
python measurement\offline_max2_parallel_measurement.py --list-ports
```

番号またはCOM名で実行:

```powershell
python measurement\offline_max2_parallel_measurement.py --com 1
python measurement\offline_max2_parallel_measurement.py --com COM8
```

## 計測スクリプト

基本実行:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8
```

主な既定値:

| 項目 | 既定値 |
|---|---:|
| ボーレート | `115200` |
| チャンネル形式 | `auto` |
| シリアル接続後の読み捨て時間 | `1.0 s` |
| 刺激前注視点 | `10.0 s` |
| 点滅刺激 | `3.0 s` |
| 刺激後注視点 | `10.0 s` |
| 点滅周波数 | `10.0 Hz` |
| 点滅開始状態 | `on` |
| 注視点半径 | `0.025` |
| 注視点色 | `0.45,0.45,0.45` |
| 刺激半径 | `0.22` |
| 表示 | フルスクリーン |

時間指定:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8 --pre-sec 5 --stim-sec 4 --post-sec 5
```

刺激周期数で指定:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8 --frequency 10 --stim-cycles 30
```

点滅開始状態:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8 --stimulus-start on
python measurement\offline_max2_parallel_measurement.py --com COM8 --stimulus-start off
```

注視点の色:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8 --fixation-color 0.45
python measurement\offline_max2_parallel_measurement.py --com COM8 --fixation-color 0.4,0.4,0.4
```

ウィンドウ表示で確認:

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM8 --windowed --window-width 1280 --window-height 720
```

## 出力ファイル

計測ごとに以下のフォルダが作られる.

```text
measurement/measurement_data/max2_parallel_YYYYMMDD_HHMMSS/
```

主なファイル:

| ファイル | 内容 |
|---|---|
| `metadata.json` | 実験条件, 刺激周波数, 開始状態, 出力ファイル一覧 |
| `events.csv` | 実験フェイズの開始/終了イベント |
| `frames.csv` | VSync後のフレーム提示時刻, 刺激ON/OFF状態 |
| `serial_samples.csv` | MAX2から受信したサンプルとPC時刻, 直近フレーム情報 |
| `max2_summary.csv` | フェイズ別, チャンネル別の局所最大/最小サマリ |

同期の考え方:

- 表示側は `glfw.swap_buffers()` 後の時刻を `frames.csv` に保存.
- シリアル側は `ser.readline()` で1行を読んだ時刻を `serial_samples.csv` に保存.
- どちらもPCの `time.perf_counter_ns()` を使う.
- 物理的なハードウェア同期ではないため, 厳密な同期が必要なら外部トリガやデバイス側タイムスタンプを検討.

## FFT解析

基本:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539
```

刺激フェイズだけ:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus
```

チャンネルと周波数範囲:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --channels ch1,ch2 --min-freq 2 --max-freq 60
```

画像保存:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --save
python analysis\FFT.py max2_parallel_20260520_185539 --save analysis\fft_result.png --no-show
```

## ウェーブレット解析

時間ごとの周波数成分を確認:

```powershell
python analysis\Wavelet.py max2_parallel_20260520_185539
```

よく使う指定:

```powershell
python analysis\Wavelet.py max2_parallel_20260520_185539 --phase stimulus --min-freq 5 --max-freq 15
python analysis\Wavelet.py max2_parallel_20260520_185539 --channels ch1,ch2 --freq-scale log
python analysis\Wavelet.py max2_parallel_20260520_185539 --save --no-show
```

## 位相タイミング解析

BPF後の波形を刺激周期ごとに折り重ね, 0-100 msのような周期内タイミングを確認する.

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539
```

10 Hz, 刺激フェイズだけ:

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --frequency 10 --phases stimulus
```

重要:

- 刺激フェイズでは `frames.csv` の実フレーム提示時刻を使う.
- シリアルサンプルは時間軸へ補間して周期ごとに切り出す.
- BPFの群遅延が分かっている場合は `--filter-delay-ms` で補正.

## フィルタ設計

刺激周波数だけを強調するIIRフィルタ候補を探索:

```powershell
python filter_design\design_peak_filter.py 10
```

出力:

- Rank 1-10の候補情報.
- `PhaseTiming.py` に貼る `DEFAULT_BANDPASS_A`, `DEFAULT_BANDPASS_B`.
- 周波数特性, 群遅延, 極配置, 時間応答のグラフ.
- グラフ左側の `R1`, `R2` チェックでRank線を表示/非表示.

JSON保存:

```powershell
python filter_design\design_peak_filter.py 10 --output filter_design\filter_10hz.json
```

候補を絞る主な条件:

| オプション | 内容 |
|---|---|
| `--max-target-gain-db 3` | target周波数のゲインが+3 dBを超える候補を除外 |
| `--max-target-delay-ms 1000` | target周波数の群遅延が大きすぎる候補を除外 |
| `--max-pole-abs 0.9998` | 極が単位円に近すぎる候補を除外 |
| `--max-q 150` | Qが高すぎる候補を除外 |

作成済みフィルタの確認:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1
python filter_design\check_filter.py analysis\PhaseTiming.py
python filter_design\check_filter.py --a "1,-3.97,5.93,-3.94,0.98" --b "0.000039,0,-0.000078,0,0.000039"
```

## よく使う作業順

1. `python measurement\offline_max2_parallel_measurement.py --list-ports`
2. `python measurement\offline_max2_parallel_measurement.py --com COM8 --frequency 10`
3. `python analysis\FFT.py <run_folder> --phase stimulus --min-freq 2 --max-freq 60`
4. `python filter_design\design_peak_filter.py 10`
5. Rank 1の係数を `analysis/PhaseTiming.py` の `DEFAULT_BANDPASS_A/B` に貼る.
6. `python analysis\PhaseTiming.py <run_folder> --frequency 10 --phases stimulus`

## トラブルシュート

### COMポートが開けない

```powershell
python measurement\offline_max2_parallel_measurement.py --list-ports
```

他のソフトが同じCOMポートを開いている場合は閉じる.

### scipyがない

```powershell
conda env update -f environment.yml --prune
conda activate eeg-max2
```

### グラフが出ない

`matplotlib` とGUIバックエンドを確認する. 保存だけなら `--save --no-show` を使う.

### 文字化けする

MarkdownとPythonファイルはUTF-8で保存する. PowerShell表示だけが崩れる場合はエディタ側で確認する.

