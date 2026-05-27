# 解析用README

このフォルダには, 計測済みデータを解析するためのコードを置く.

現在の主な解析コードは `FFT.py`, `Wavelet.py`, `PhaseTiming.py`,
`PhaseTiming_ch1_minus_ch2.py`.
`measurement/measurement_data/<run folder>/serial_samples.csv` などを読み込み,
チャンネルごとの周波数成分, 時間-周波数変化, 刺激周期に対する位相を確認する.

## ファイル説明

| ファイル | 内容 |
| :--- | :--- |
| [analysis_README.md](analysis_README.md) | 解析コード全体の説明 |
| [FFT.py](FFT.py) | チャンネル別の波形表示とFFT解析 |
| [Wavelet.py](Wavelet.py) | チャンネル別の時間-周波数解析 |
| [PhaseTiming.py](PhaseTiming.py) | 刺激周期ごとの位相タイミング確認 |
| [PhaseTiming_ch1_minus_ch2.py](PhaseTiming_ch1_minus_ch2.py) | `ch1 - ch2` の差分信号で位相タイミング確認 |

## 目次

- [前提](#前提)
- [入力データ](#入力データ)
- [基本実行](#基本実行)
- [グラフの見方](#グラフの見方)
- [解析区間の指定](#解析区間の指定)
- [チャンネル指定](#チャンネル指定)
- [周波数範囲の指定](#周波数範囲の指定)
- [目標周波数の指定](#目標周波数の指定)
- [画像保存](#画像保存)
- [処理内容](#処理内容)
- [よくある実行例](#よくある実行例)
- [ウェーブレット変換](#ウェーブレット変換)
- [位相タイミング確認](#位相タイミング確認)
- [注意](#注意)

## 前提

FFT表示には `numpy` と `matplotlib` を使用する.

conda環境を更新する場合:

```powershell
conda env update -f environment.yml --prune
conda activate eeg-max2
```

個別に入れる場合:

```powershell
conda install matplotlib
```

## 入力データ

FFT.py は以下のファイルを読む.

```text
serial_samples.csv
metadata.json
```

`serial_samples.csv` から以下を使用する.

| 列名 | 用途 |
|---|---|
| `experiment_time_s` | サンプル時刻 |
| `phase_name` | 解析区間の選択 |
| `ch1`, `ch2`, `ch3` | 各チャンネルの計測値 |
| `parse_error` | エラー行の除外 |

`metadata.json` がある場合, 刺激周波数を読み取り,
FFTやWaveletのターゲット周波数計算に使う.
FFTグラフでは見やすさを優先し, 目的周波数の縦点線は表示しない.

## 基本実行

計測フォルダ名だけで指定:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539
```

フルパスで指定:

```powershell
python analysis\FFT.py C:\Users\g2110\Documents\EEG\26_EEG\measurement\measurement_data\max2_parallel_20260520_185539
```

`serial_samples.csv` を直接指定してもよい:

```powershell
python analysis\FFT.py C:\Users\g2110\Documents\EEG\26_EEG\measurement\measurement_data\max2_parallel_20260520_185539\serial_samples.csv
```

## グラフの見方

グラフは2つ表示され, それぞれの中にチャンネル別の線が重ねて表示される.
各線のラベルは凡例に表示される.

| 場所 | 内容 |
|---|---|
| 左側 | チャンネル別の時系列波形 |
| 右側 | チャンネル別のFFT結果. 全チャンネル最大を1.0にした相対振幅 |
| 色付きの線 | `ch1`, `ch2`, `ch3` の各チャンネル |
| 色付きの丸印 | 各チャンネルのピーク周波数 |
| 左側のShowボタン | チャンネル線の表示/非表示を切り替える |

実行時のコンソールにも, チャンネルごとのサンプル数,
推定サンプリング周波数, ピーク周波数, 絶対振幅, 相対振幅が出力される.
相対振幅は, 選択された全チャンネルのFFT振幅最大値を `1.0` とする.

## 解析区間の指定

既定では `all` で計測データ全体を解析する.

```powershell
python analysis\FFT.py max2_parallel_20260520_185539
```

刺激区間だけを解析:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus
```

指定可能な区間:

```text
all
idle
fixation_before
stimulus
fixation_after
finished
```

さらに, phaseで抽出したデータの中から秒数範囲を指定できる.
既定では `--time-range` の0秒は「選択されたデータの先頭」.

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus --time-range 0,3
```

実験全体の `experiment_time_s` を基準にしたい場合:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --time-origin experiment --time-range 10,13
```

## チャンネル指定

全チャンネルを表示:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539
```

一部チャンネルのみ表示:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --channels ch1
python analysis\FFT.py max2_parallel_20260520_185539 --channels ch1,ch3
```

## 周波数範囲の指定

FFT表示とピーク検出の範囲を指定:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --min-freq 2 --max-freq 60
```

低周波のゆっくりした変動をピーク検出から外したい場合は,
`--min-freq` を上げる.

## 目標周波数の指定

通常は `metadata.json` の刺激周波数を自動で使う.

手動で目標周波数を指定:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --target-freq 10
```

目標周波数はコンソールの `target_amplitude` と `target_relative_amplitude` の計算に使う.
FFTグラフには目的周波数の縦点線を表示しない.

目標周波数計算自体を使わない:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --no-target-marker
```

## 画像保存

グラフを表示しつつ, 計測フォルダ内にPNG保存:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --save
```

保存先を指定:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --save analysis\fft_result.png
```

表示せずに計算だけ実行:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --no-show
```

表示せずにPNG保存:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --save --no-show
```

## 処理内容

FFT.py は以下の順で処理する.

1. 計測フォルダを探す
2. `serial_samples.csv` を読み込む
3. `parse_error` がある行を除外
4. 指定した `phase_name` の行だけを抽出
5. チャンネルごとに時系列データを作成
6. `--time-range` が指定されていれば時間範囲で切り出す
7. `experiment_time_s` からサンプリング周波数を推定
8. 不等間隔サンプルを等間隔に補間
9. 平均値を引いてHanning窓をかける
10. `numpy.fft.rfft` でFFT
11. 全チャンネルの最大FFT振幅を基準に相対振幅化
12. チャンネルごとに色分けして波形とFFTを重ねて表示

## よくある実行例

刺激区間だけを 2-60 Hz で確認:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus --min-freq 2 --max-freq 60
```

刺激区間の最初の3秒だけを確認:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus --time-range 0,3
```

10 Hz 付近を見たい場合:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --min-freq 5 --max-freq 15 --target-freq 10
```

ch1とch2だけを保存:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --channels ch1,ch2 --save
```

## ウェーブレット変換

時間ごとの周波数成分の変化を見たい場合は `Wavelet.py` を使う.
FFTは解析区間全体の周波数成分を見る方法,
ウェーブレット変換は「いつ, 何Hzが強いか」を見る方法.

基本実行:

```powershell
python analysis\Wavelet.py max2_parallel_20260520_185539
```

フルパス指定:

```powershell
python analysis\Wavelet.py C:\Users\g2110\Documents\EEG\26_EEG\measurement\measurement_data\max2_parallel_20260520_185539
```

既定では `all` で計測データ全体を解析し,
Morletウェーブレットで 2-45 Hz を見る.

グラフの構成:

| 場所 | 内容 |
|---|---|
| 一番上 | チャンネル別の時系列波形 |
| 下側 | チャンネルごとの時間-周波数パワー |
| 赤い点線 | 刺激周波数, または `--target-freq` で指定した周波数 |
| 白い丸印 | 各チャンネルの最大パワー位置 |

カラーマップは既定で虹色系の `turbo`.
色の基準は既定で `relative`.
これは選択された全チャンネルのWavelet powerをまとめて,
5-95 percentile の範囲を `0-1` に正規化する表示.
チャンネルごとではなく全チャンネル共通の相対基準なので,
チャンネル間の強さも比較しやすい.

よく使う指定:

```powershell
python analysis\Wavelet.py max2_parallel_20260520_185539 --min-freq 5 --max-freq 15
python analysis\Wavelet.py max2_parallel_20260520_185539 --channels ch1,ch2
python analysis\Wavelet.py max2_parallel_20260520_185539 --freq-count 80
python analysis\Wavelet.py max2_parallel_20260520_185539 --freq-scale log
python analysis\Wavelet.py max2_parallel_20260520_185539 --target-freq 10
python analysis\Wavelet.py max2_parallel_20260520_185539 --colormap turbo
python analysis\Wavelet.py max2_parallel_20260520_185539 --power-scale relative
python analysis\Wavelet.py max2_parallel_20260520_185539 --relative-vmin-percentile 1 --relative-vmax-percentile 99
python analysis\Wavelet.py max2_parallel_20260520_185539 --notch on
python analysis\Wavelet.py max2_parallel_20260520_185539 --save
python analysis\Wavelet.py max2_parallel_20260520_185539 --no-show
```

主なオプション:

| オプション | 内容 |
|---|---|
| `--phase` | 解析区間 |
| `--channels` | 表示するチャンネル |
| `--min-freq` | 最小周波数 |
| `--max-freq` | 最大周波数 |
| `--freq-count` | 周波数分解数 |
| `--freq-scale` | `linear` または `log` |
| `--wavelet-cycles` | Morletウェーブレットの周期数 |
| `--edge-ignore-sec` | ピーク検出で無視する端の秒数 |
| `--power-scale` | `relative`, `db`, `linear` |
| `--relative-vmin-percentile` | 相対表示の下側percentile |
| `--relative-vmax-percentile` | 相対表示の上側percentile |
| `--colormap` | `turbo`, `jet`, `rainbow` などMatplotlib colormap名 |
| `--notch` | `on` で既定IIRノッチをWavelet前に適用 |
| `--save` | PNG保存 |
| `--no-show` | グラフを表示せず計算だけ実行 |

`--wavelet-cycles` を大きくすると周波数分解能が上がるが,
時間方向の変化は鈍くなる.
小さくすると時間変化を追いやすいが,
周波数方向は粗くなる.

## 位相タイミング確認

刺激周期ごとにBPF後の波形を切り出して重ね描きし,
刺激位相に対して計測波形の山がどこに来るかを確認する場合は
`PhaseTiming.py` を使う.

例: 10 Hz の場合は 1周期が 100 ms.
ONスタートならONになった瞬間を 0 ms として,
100-200 ms, 200-300 ms, 300-400 ms のような各周期を
0-100 ms の横軸に折り重ねて表示する.

基本実行:

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539
```

フルパス指定:

```powershell
python analysis\PhaseTiming.py C:\Users\g2110\Documents\EEG\26_EEG\measurement\measurement_data\max2_parallel_20260520_185539
```

重要:

- フェイズ境界は `events.csv` ではなく, 原則 `frames.csv` のフレーム提示時刻を使う.
- `frames.csv` の `experiment_time_s` はVSync後の提示タイミングに近い値.
- `stimulus_start` イベント時刻と, 実際の最初の刺激フレーム提示時刻は少しズレることがある.
- 位相0 msは, 刺激フェイズの最初の提示フレーム時刻を基準にする.

グラフの構成:

| グラフ | 内容 |
|---|---|
| Bandpass filtered signal | BPF後の全体波形. フェイズ境界も表示 |
| folded filtered waveform | 1周期ぶんに折り重ねた波形 |
| peak timing flow | 各周期内で最大値が出た時刻の推移 |

フェイズごと, チャンネルごとにグラフが作られる.
既定では以下を出す.

```text
fixation_before
stimulus
fixation_after
```

よく使う指定:

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --phases stimulus
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --channels ch1,ch2
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --frequency 10
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --start-state on
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --start-state off
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --peak-mode abs
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --filter-delay-ms 12.5
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --notch on
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --save
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --no-show
```

`--notch on` を指定すると, `PhaseTiming.py` 冒頭の
`DEFAULT_NOTCH_A`, `DEFAULT_NOTCH_B` を使ってIIRノッチをBPF前に適用する.
既定はOFF.

グラフの振幅軸は, 選択された全チャンネルの最大絶対振幅を基準に共通化する.
コンソールには `Amplitude reference` として基準値を表示する.

`ch1 - ch2` を1つの指標として見たい場合は `PhaseTiming_ch1_minus_ch2.py` を使う.

```powershell
python analysis\PhaseTiming_ch1_minus_ch2.py max2_parallel_20260520_185539
python analysis\PhaseTiming_ch1_minus_ch2.py max2_parallel_20260520_185539 --notch on
python analysis\PhaseTiming_ch1_minus_ch2.py max2_parallel_20260520_185539 --positive-channel ch1 --negative-channel ch2
```

BPF係数:

`PhaseTiming.py` 冒頭の `DEFAULT_BANDPASS_A` と `DEFAULT_BANDPASS_B` に書く.
別プロジェクトで作成した刺激周波数用の係数をここへ入れる.
実行時に一時指定する場合:

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --filter-a "1,-3.97,5.93,-3.94,0.98" --filter-b "0.000039,0,-0.000078,0,0.000039"
```

BPFは位相遅れを作ることがある.
係数の群遅延が分かっている場合は `--filter-delay-ms` で手動補正する.
正の値を指定すると, フィルタ後の波形をその分だけ前に戻して位相確認する.

## 注意

- `matplotlib` がない場合, グラフ表示時にエラーになる.
- `--no-show` だけならグラフ表示せず計算結果だけ確認できる.
- 計測データにゆっくりしたドリフトがある場合, 低周波側に大きいピークが出ることがある.
- 刺激周波数だけを見たい場合, `--min-freq` と `--max-freq` を狭くすると見やすい.
