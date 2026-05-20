# 解析用README

このフォルダには, 計測済みデータを解析するためのコードを置く.

現在の主な解析コードは `FFT.py`.
`measurement/measurement_data/<run folder>/serial_samples.csv` を読み込み,
チャンネルごとに線色を変えて, 時系列波形とFFTグラフを表示する.

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
FFTグラフに目標周波数の縦線を表示する.

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
| 右側 | チャンネル別のFFT結果 |
| 色付きの線 | `ch1`, `ch2`, `ch3` の各チャンネル |
| 色付きの丸印 | 各チャンネルのピーク周波数 |
| 赤い点線 | 刺激周波数, または `--target-freq` で指定した周波数 |

実行時のコンソールにも, チャンネルごとのサンプル数,
推定サンプリング周波数, ピーク周波数, 振幅が出力される.

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

目標周波数の縦線を表示しない:

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
6. `experiment_time_s` からサンプリング周波数を推定
7. 不等間隔サンプルを等間隔に補間
8. 平均値を引いてHanning窓をかける
9. `numpy.fft.rfft` でFFT
10. チャンネルごとに色分けして波形とFFTを重ねて表示

## よくある実行例

刺激区間だけを 2-60 Hz で確認:

```powershell
python analysis\FFT.py max2_parallel_20260520_185539 --phase stimulus --min-freq 2 --max-freq 60
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
Morletウェーブレットで 2-60 Hz を見る.

グラフの構成:

| 場所 | 内容 |
|---|---|
| 一番上 | チャンネル別の時系列波形 |
| 下側 | チャンネルごとの時間-周波数パワー |
| 赤い点線 | 刺激周波数, または `--target-freq` で指定した周波数 |
| 白い丸印 | 各チャンネルの最大パワー位置 |

よく使う指定:

```powershell
python analysis\Wavelet.py max2_parallel_20260520_185539 --min-freq 5 --max-freq 15
python analysis\Wavelet.py max2_parallel_20260520_185539 --channels ch1,ch2
python analysis\Wavelet.py max2_parallel_20260520_185539 --freq-count 80
python analysis\Wavelet.py max2_parallel_20260520_185539 --freq-scale log
python analysis\Wavelet.py max2_parallel_20260520_185539 --target-freq 10
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
| `--power-scale` | `db` または `linear` |
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
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --save
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --no-show
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
