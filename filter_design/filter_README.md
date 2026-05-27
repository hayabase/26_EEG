# filter_design README

このフォルダは, 刺激周波数を強調するIIRフィルタを設計し, そのフィルタ特性を確認するためのコードを置く場所.

SSVEP解析では, 例えば10 Hz刺激なら10 Hz付近の成分を見たい. そのために `design_peak_filter.py` で候補フィルタを探索し, `check_filter.py` で周波数特性, 群遅延, 極配置, 時間応答を確認する.

## ファイル説明

| ファイル | 内容 |
| :--- | :--- |
| [filter_README.md](filter_README.md) | フィルタ設計コード全体の説明 |
| [design_peak_filter.py](design_peak_filter.py) | target周波数を強調するIIRピーク/BPF候補を探索 |
| [design_bandpass_filter.py](design_bandpass_filter.py) | 通過域, 遷移域を直接指定してIIR BPFを設計 |
| [design_fir_bandpass_filter.py](design_fir_bandpass_filter.py) | 通過域, 遷移域を直接指定してFIR BPFを設計 |
| [design_iir_notch_filter.py](design_iir_notch_filter.py) | 中心周波数とQを指定してIIRノッチフィルタを設計 |
| [check_filter.py](check_filter.py) | 作成済み係数の周波数特性, 群遅延, 極配置を確認 |
| `_tmp_auto_check.txt` | 自動確認時の一時出力, 必要なければ削除可能 |

## 目次

- [使う前の準備](#使う前の準備)
- [design_peak_filter.py](#design_peak_filterpy)
- [design_bandpass_filter.py](#design_bandpass_filterpy)
- [design_fir_bandpass_filter.py](#design_fir_bandpass_filterpy)
- [design_iir_notch_filter.py](#design_iir_notch_filterpy)
- [探索条件](#探索条件)
- [グラフの見方](#グラフの見方)
- [コンソール出力の見方](#コンソール出力の見方)
- [PhaseTiming.pyへ係数を入れる](#phasetimingpyへ係数を入れる)
- [check_filter.py](#check_filterpy)
- [用語](#用語)
- [よくある流れ](#よくある流れ)
- [トラブルシュート](#トラブルシュート)

## 使う前の準備

プロジェクトルートで環境を有効化する.

```powershell
conda activate eeg-max2
```

環境を更新する場合:

```powershell
conda env update -f environment.yml --prune
```

必要な主なライブラリ:

| ライブラリ | 用途 |
|---|---|
| `numpy` | 係数, FFT, 配列計算 |
| `scipy` | IIRフィルタ設計, 周波数応答, 群遅延 |
| `matplotlib` | グラフ表示 |

確認:

```powershell
python -c "import numpy, scipy, matplotlib; print('ok')"
```

## design_peak_filter.py

### 目的

指定した刺激周波数を通し, それ以外を減衰させるIIRフィルタ候補を探索する.

例:

- 10 Hz刺激なら10 Hz付近を強調.
- 7.5 Hz刺激なら7.5 Hz付近を強調.
- 候補はRank順に表示.
- Rank 1の係数を `PhaseTiming.py` の `DEFAULT_BANDPASS_A/B` に貼って使える.

### 基本実行

10 Hz用フィルタを探索:

```powershell
python filter_design\design_peak_filter.py 10
```

7.5 Hz用フィルタを探索:

```powershell
python filter_design\design_peak_filter.py 7.5
```

サンプリング周波数も指定:

```powershell
python filter_design\design_peak_filter.py 10 1000
```

設計法も指定:

```powershell
python filter_design\design_peak_filter.py 10 1000 butter
python filter_design\design_peak_filter.py 10 1000 cheby1
python filter_design\design_peak_filter.py 10 1000 cheby2
python filter_design\design_peak_filter.py 10 1000 ellip
```

引数なしで実行すると, コード冒頭の既定値だけで実行する.
対話入力したい場合は `--interactive` を付ける.

```powershell
python filter_design\design_peak_filter.py
python filter_design\design_peak_filter.py --interactive
```

### よく使うコマンドまとめ

基本:

```powershell
python filter_design\design_peak_filter.py 10
```

10Hzでの群遅延を1 ms以下に制限:

```powershell
python filter_design\design_peak_filter.py 10 --target-delay-ms 1
```

同じ意味の正式名:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 1
```

候補が出ない場合, 遅延制限を少し緩める:

```powershell
python filter_design\design_peak_filter.py 10 --target-delay-ms 10
python filter_design\design_peak_filter.py 10 --target-delay-ms 50
```

鋭い候補を総当たりで探す:

```powershell
python filter_design\design_peak_filter.py 10 --search-preset sharp --max-target-delay-ms 10 --target-delay-neighborhood-width 1
```

`--search-preset sharp` は `family`, 通過帯域端, stopband gap, `gpass`, `gstop`
をまとめて振る. さらに広く探す場合は `--search-preset exhaustive` を使う.
ただし実行時間は長くなる.

グラフなしでコンソール出力だけ確認:

```powershell
python filter_design\design_peak_filter.py 10 --no-plot --no-check-filter
```

進捗表示も消して短く実行:

```powershell
python filter_design\design_peak_filter.py 10 --no-plot --no-check-filter --no-progress
```

JSON保存:

```powershell
python filter_design\design_peak_filter.py 10 --output filter_design\peak_10hz.json
```

保存したJSONを確認:

```powershell
python filter_design\check_filter.py --json filter_design\peak_10hz.json --rank 1
```

### JSON保存

候補と係数を保存:

```powershell
python filter_design\design_peak_filter.py 10 --output filter_design\filter_10hz.json
```

グラフを保存:

```powershell
python filter_design\design_peak_filter.py 10 --save-figure filter_design\filter_10hz.png
```

グラフ表示なし:

```powershell
python filter_design\design_peak_filter.py 10 --no-plot
```

進捗表示なし:

```powershell
python filter_design\design_peak_filter.py 10 --no-progress
```

## design_bandpass_filter.py

### 目的

通過域と遷移域を直接決めて, 仕様どおりのBPFを作る.
`design_peak_filter.py` はtarget周波数中心の探索用.
こちらは `9.5-10.5 Hzを通過域, その外側3 Hzを遷移域` のように明示して設計する.

グラフ表示は, 周波数特性, 群遅延, 極配置, 時間波形を1枚で表示する.
周波数特性では通過域を緑, 遷移域を橙, 阻止域を灰色で表示する.
左側の `R1`, `R2` などのチェックで, Rankごとの線を表示, 非表示にできる.

### 基本実行

対話入力で実行:

```powershell
python filter_design\design_bandpass_filter.py
```

通過域と遷移域を指定:

```powershell
python filter_design\design_bandpass_filter.py --passband 9.5,10.5 --transition 3.0
```

低周波側と高周波側の遷移域を別々に指定:

```powershell
python filter_design\design_bandpass_filter.py --pass-low 9.5 --pass-high 10.5 --transition-low 2.0 --transition-high 3.0
```

省略形:

```powershell
python filter_design\design_bandpass_filter.py 9.5 10.5 --transition 3.0
```

設計法を絞る:

```powershell
python filter_design\design_bandpass_filter.py --passband 9.5,10.5 --transition 3.0 --families butter,ellip
```

より鋭くする例:

```powershell
python filter_design\design_bandpass_filter.py --passband 9.5,10.5 --transition 1.0 --gstop 80
```

ただし, 遷移域を狭くしすぎたり `gstop` を大きくしすぎると, IIRの次数が上がる.
このプロジェクトでは `PhaseTiming.py` へ貼りやすい直接形a,bを出すため, 高次数では極が1を超える数値不安定判定になり候補から除外されることがある.

保存:

```powershell
python filter_design\design_bandpass_filter.py --passband 9.5,10.5 --transition 3.0 --save-json filter_design\bpf_10hz.json --save-figure filter_design\bpf_10hz.png
```

JSONは `check_filter.py` で確認できる:

```powershell
python filter_design\check_filter.py --json filter_design\bpf_10hz.json --rank 1
```

係数出力:

```powershell
python filter_design\design_bandpass_filter.py --print-coefficients best
python filter_design\design_bandpass_filter.py --print-coefficients all
python filter_design\design_bandpass_filter.py --print-coefficients none
```

`--print-coefficients all` ではランキング全候補の `a,b` を出力する.

## design_fir_bandpass_filter.py

### 目的

通過域と遷移域を直接決めて, FIRのBPFを作る.
IIR版と同じく `9.5-10.5 Hzを通過域, その外側3 Hzを遷移域` のように指定する.

FIR版では分母係数は常に `a = (1,)` になる.
IIRのような再帰極はないため, 極の安定性や `max_pole_abs` は評価しない.
代わりに以下を見る.

- `numtaps`: FIRタップ数
- `order`: `numtaps - 1`
- `constant group delay`: 線形位相FIRの一定群遅延
- `symmetry_error`: 係数が左右対称か
- `linear_phase`: 線形位相か
- `meets_spec`: 通過域/阻止域仕様を満たすか

グラフ表示は, 周波数特性, 一定群遅延, FIRタップ係数, 時間波形を1枚で表示する.
IIR版の極配置グラフはFIR版ではタップ係数グラフに置き換えている.

### 基本実行

対話入力で実行:

```powershell
python filter_design\design_fir_bandpass_filter.py
```

通過域と遷移域を指定:

```powershell
python filter_design\design_fir_bandpass_filter.py --passband 9.5,10.5 --transition 3.0
```

省略形:

```powershell
python filter_design\design_fir_bandpass_filter.py 9.5 10.5 --transition 3.0
```

設計法を絞る:

```powershell
python filter_design\design_fir_bandpass_filter.py --methods firwin_hamming,remez
```

タップ数を指定:

```powershell
python filter_design\design_fir_bandpass_filter.py --numtaps 751
```

複数タップ数を比較:

```powershell
python filter_design\design_fir_bandpass_filter.py --tap-counts 501,751,1001
```

最大遅延で候補を制限:

```powershell
python filter_design\design_fir_bandpass_filter.py --passband 9,11 --transition 3.0 --max-delay-ms 10
```

JSON保存:

```powershell
python filter_design\design_fir_bandpass_filter.py --save-json filter_design\fir_bpf_10hz.json
```

グラフ保存:

```powershell
python filter_design\design_fir_bandpass_filter.py --save-figure filter_design\fir_bpf_10hz.png --no-show
```

係数出力:

```powershell
python filter_design\design_fir_bandpass_filter.py --print-coefficients best
python filter_design\design_fir_bandpass_filter.py --print-coefficients all
python filter_design\design_fir_bandpass_filter.py --print-coefficients none
```

FIRはタップ数が多く, `--print-coefficients all` は出力が長くなる.
全候補の係数を保存したい場合は `--save-json` を推奨.

### FIRで注意すること

FIRは線形位相にしやすく安定性も扱いやすいが, 狭帯域BPFではタップ数が大きくなりやすい.
タップ数が大きいほど遅延も増える.

線形位相FIRの群遅延は概ね次の式になる.

```text
delay_ms = (numtaps - 1) / 2 / samplerate * 1000
```

例: `samplerate=1000 Hz`, `numtaps=747` の場合:

```text
(747 - 1) / 2 / 1000 * 1000 = 373 ms
```

リアルタイム処理で遅延が問題になる場合は, IIR版も比較する.
オフライン解析で位相を安定して見たい場合は, FIR版が読みやすい候補になる.

`--max-delay-ms` を指定すると, この式から許容される最大タップ数を計算し,
その範囲内の候補だけを評価する.
例えば `samplerate=1000 Hz`, `--max-delay-ms 10` では最大 `21 taps`.
ただし狭帯域BPFでは, 10 ms以下のFIRは仕様を満たさない場合が多い.

## design_iir_notch_filter.py

### 目的

特定の周波数だけを落とすIIRノッチフィルタを設計する.
50 Hz/60 Hzの電源ノイズ除去などを想定している.

ノッチ版は `scipy.signal.iirnotch` を使う2次IIRフィルタで,
中心周波数とQを指定する.
Qが大きいほどノッチ幅は狭くなり, 周辺周波数への影響は小さくなる.

### 基本実行

50 Hzノッチ:

```powershell
python filter_design\design_iir_notch_filter.py --target-freq 50
```

60 Hzノッチ:

```powershell
python filter_design\design_iir_notch_filter.py 60
```

Q値を複数比較:

```powershell
python filter_design\design_iir_notch_filter.py --target-freq 50 --q-values 20,30,50,100
```

ノッチ幅からQを指定:

```powershell
python filter_design\design_iir_notch_filter.py --target-freq 50 --bandwidth-hz 2
```

JSON保存:

```powershell
python filter_design\design_iir_notch_filter.py --target-freq 50 --save-json filter_design\notch_50hz.json
```

係数出力:

```powershell
python filter_design\design_iir_notch_filter.py --print-coefficients best
python filter_design\design_iir_notch_filter.py --print-coefficients all
python filter_design\design_iir_notch_filter.py --print-coefficients none
```

### ノッチで注意すること

IIRノッチの係数は `a,b` ともに通常3個で, `order=2`.
中心周波数には零点が置かれるため, `gain_at_50Hz` などは非常に小さい値になる.

Qとノッチ幅の目安:

```text
notch_bandwidth_hz = target_freq / Q
```

例: `target_freq=50 Hz`, `Q=50` の場合:

```text
50 / 50 = 1 Hz
```

位相タイミング解析でノッチを使う場合, ノッチ周辺では群遅延が大きく見えることがある.
オフライン解析で位相を重視するなら, `filtfilt` のようなゼロ位相処理も比較する.

## 探索条件

主な既定値:

| 設定 | 既定値 | 意味 |
|---|---:|---|
| `--samplerate` | `1000` | EEGサンプリング周波数[Hz] |
| `--target-freq` | `10` | 強調したい周波数[Hz] |
| `--family` | `butter` | IIR設計法 |
| `--search-preset` | `standard` | 探索プリセット. `sharp`/`exhaustive`で総当たり |
| `--families` | なし | 複数family探索. 例 `butter,cheby2,ellip` |
| `--passband-search-width` | `1.5` | target周波数周辺で通過帯域候補を探す幅[Hz] |
| `--passband-edge-step` | `0.05` | 通過帯域端の探索刻み[Hz] |
| `--passband-offset-values` | なし | targetから通過帯域端までの距離候補[Hz] |
| `--passband-search-width-values` | なし | 探索幅を複数振る候補[Hz] |
| `--passband-edge-step-values` | なし | 刻み幅を複数振る候補[Hz] |
| `--stopband-gap-min` | `0.5` | 通過帯域と阻止帯域の最小間隔[Hz] |
| `--stopband-gap-max` | `4.0` | 通過帯域と阻止帯域の最大間隔[Hz] |
| `--stopband-gap-step` | `0.25` | 阻止帯域間隔の探索刻み[Hz] |
| `--stopband-gap-values` | なし | stopband gapを直接総当たりする候補[Hz] |
| `--gpass-values` | `1` | 通過域端最大損失[dB] |
| `--gstop-values` | `20,60,80,100,150,200` | 阻止域端最小減衰[dB] |
| `--acceptable-gain-db` | `-1` | target周波数で許容する最小ゲイン[dB] |
| `--max-target-gain-db` | `3` | target周波数で許容する最大ゲイン[dB] |
| `--target-neighborhood-width` | `0.5` | target周波数周辺ゲインを確認する半幅[Hz] |
| `--max-near-target-gain-db` | `3` | target周辺範囲で許容する最大ゲイン[dB] |
| `--max-q` | `150` | Q値の上限 |
| `--max-target-delay-ms` | `1000` | target周波数と周辺で許容する最大群遅延[ms] |
| `--target-delay-neighborhood-width` | `1.0` | 群遅延上限を確認するtarget周波数周辺の半幅[Hz] |
| `--target-delay-neighborhood-points` | `401` | 周辺群遅延を走査する点数 |
| `--time-response-duration-ms` | `3000` | 短時間応答確認のシミュレーション時間[ms] |
| `--time-response-start-ms` | `2000` | 短時間応答ゲインを評価する開始時刻[ms] |
| `--time-response-window-ms` | `1000` | 短時間応答ゲインを評価する窓長[ms] |
| `--min-time-response-gain-db` | `-3` | 短時間応答で許容する最小ゲイン[dB] |
| `--rise-time-threshold-db` | `-3` | 立ち上がり時間として記録する到達しきい値[dB] |
| `--rise-time-window-ms` | `200` | 立ち上がり時間を判定する移動RMS窓長[ms] |
| `--no-time-response-gain-check` | OFF | 短時間応答ゲインの足切りを無効化 |
| `--max-pole-abs` | `0.9998` | 極の絶対値上限 |
| `--max-direct-form-order` | `10` | a,b直接形の最大次数 |
| `--top-n` | `10` | 表示する候補数 |

### 条件をゆるめる例

候補が出ない場合:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 0 --max-q 0
```

`0` 以下を指定すると制限なしになる項目がある.

target周波数の群遅延を1 ms以下に絞る場合:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 1
```

既定では `target_freq ± 1.0 Hz` の範囲も走査し, その範囲内の最大絶対群遅延が
`--max-target-delay-ms` を超える候補も除外する.
周辺幅を変える場合:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 10 --target-delay-neighborhood-width 1
```

または短い別名:

```powershell
python filter_design\design_peak_filter.py 10 --target-delay-ms 1
```

1 msは指定可能だが, 鋭いIIR BPFではかなり厳しい.
候補がゼロになる場合は `--max-target-delay-ms 10`, `--max-target-delay-ms 50` のように段階的に緩める.

周波数特性では0 dBに近いのに時間波形で小さく見える場合は, 高Qフィルタの立ち上がりが遅い.
`design_peak_filter.py` は既定で3秒入力の最後1秒を見て, `--min-time-response-gain-db -3`
未満の候補を除外する. この確認を外す場合:

```powershell
python filter_design\design_peak_filter.py 10 --no-time-response-gain-check
```

### 条件を厳しくする例

鋭さを優先して広く探す:

```powershell
python filter_design\design_peak_filter.py 10 --search-preset sharp --max-target-delay-ms 10 --target-delay-neighborhood-width 1
```

手動で総当たり範囲を指定する:

```powershell
python filter_design\design_peak_filter.py 10 --families butter,cheby2,ellip --passband-offset-values 0.02,0.03,0.05,0.075,0.1,0.15,0.2 --stopband-gap-values 0.05,0.1,0.2,0.5,1.0 --gpass-values 0.5,1,2 --gstop-values 20,40,60,80 --max-target-delay-ms 10 --target-delay-neighborhood-width 1
```

target周波数の過剰な増幅を避ける:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-gain-db 1
```

target周波数の一点だけでなく, 周辺の鋭い盛り上がりも避ける:

```powershell
python filter_design\design_peak_filter.py 10 --target-neighborhood-width 0.5 --max-near-target-gain-db 3
```

`--target-neighborhood-width 0.5` は `target_freq ± 0.5 Hz` の範囲を確認する.
この範囲内の最大ゲインが `--max-near-target-gain-db` を超える候補は除外される.

遅延が大きい候補を避ける:

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 300
```

極が単位円に近すぎる候補を避ける:

```powershell
python filter_design\design_peak_filter.py 10 --max-pole-abs 0.999
```

次数を低く抑える:

```powershell
python filter_design\design_peak_filter.py 10 --max-direct-form-order 8
```

## グラフの見方

`design_peak_filter.py` は候補探索後に, Rank候補を重ねた詳細グラフを表示する.

主なグラフ:

| グラフ | 内容 |
|---|---|
| Frequency response | 周波数特性. target周波数でどれだけ通るかを見る |
| Group delay | 群遅延. target周波数付近の遅れを見る |
| Pole-zero plot | 極と零点. 極が単位円内にあるかを見る |
| Time waveform | テスト信号を入れたときの出力波形を見る |

周波数特性の凡例は, 線と重なって見にくくなるため既定では非表示.
Rankごとの表示, 非表示は左側の `line` パネルで操作する.
周波数特性内にも凡例を出したい場合:

```powershell
python filter_design\design_peak_filter.py 10 --show-response-legend
```

`design_fir_bandpass_filter.py` では, `Pole-zero plot` の代わりに `FIR impulse response / taps` を表示する.
FIRは `a=(1,)` の非再帰フィルタなので, IIRのような安定性確認用の極プロットより, タップ係数の対称性と一定群遅延を見る.

左側の `line` パネル:

- `R1`, `R2`, ... はRank番号.
- チェックを外すと, そのRankの線が全グラフで非表示.
- もう一度チェックすると再表示.
- `target` や `-3 dB` などの補助線は対象外.

## コンソール出力の見方

例:

```text
Rank 1:
  family: butter
  prototype_order: 3
  direct_form_order: 6
  fp: 9.9000 Hz - 10.0500 Hz
  fs: 7.7000 Hz - 12.2500 Hz
  gpass: 1.00 dB
  gstop: 80.00 dB
  gain_at_10Hz: 0.02 dB
  near_target_max_gain: 0.03 dB at 9.9500 Hz
  bandwidth_3db: 0.1563 Hz
  Q: 64.00
  target_delay: 568.12 ms
  near_target_max_delay: 820.45 ms at 9.1200 Hz
  time_response_gain: -1.20 dB (0.871x)
  rise_time: 1814.00 ms to -3.00 dB
  max_pole_abs: 0.99970589
```

| 項目 | 意味 |
|---|---|
| `family` | 設計法 |
| `prototype_order` | scipy設計時のプロトタイプ次数 |
| `direct_form_order` | a,b係数として実装するときの次数 |
| `fp` | 通過帯域端 |
| `fs` | 阻止帯域端 |
| `gpass` | 通過域端最大損失 |
| `gstop` | 阻止域端最小減衰 |
| `gain_at_10Hz` | target周波数でのゲイン |
| `near_target_max_gain` | target周辺範囲での最大ゲイン |
| `bandwidth_3db` | -3 dB帯域幅 |
| `Q` | 鋭さ. 高いほど狭帯域 |
| `target_delay` | target周波数での群遅延 |
| `near_target_max_delay` | target周辺範囲での最大絶対群遅延 |
| `time_response_gain` | 短時間の因果フィルタ出力で実際に出たRMSゲイン |
| `rise_time` | 因果フィルタ出力が指定dBしきい値に初めて到達した時刻 |
| `max_pole_abs` | 極の絶対値最大. 1未満なら理論上安定 |

FIR版で追加される主な項目:

| 項目 | 意味 |
|---|---|
| `method` | FIR設計法. `firwin_hamming`, `firwin_kaiser`, `remez`, `firls` など |
| `window` | 窓関数または設計タイプ |
| `numtaps` | FIR係数の数 |
| `order` | FIR次数. `numtaps - 1` |
| `constant group delay` | 線形位相FIRの一定群遅延 |
| `symmetry_error` | タップ係数の左右対称誤差 |
| `linear_phase` | 線形位相とみなせるか |
| `meets_spec` | 通過域/阻止域仕様を満たしたか |

## PhaseTiming.pyへ係数を入れる

`design_peak_filter.py`, `design_bandpass_filter.py`, `design_fir_bandpass_filter.py`
の最後に以下のような出力が出る.

```python
DEFAULT_BANDPASS_A = (
    1,
    -5.98,
    ...
)

DEFAULT_BANDPASS_B = (
    2.05e-10,
    0,
    ...
)
```

これを `analysis/PhaseTiming.py` 冒頭の同名定数へ貼る.
`PhaseTiming_ch1_minus_ch2.py` も `PhaseTiming.py` の係数を参照する.

`design_peak_filter.py` では, Rank 1の `DEFAULT_BANDPASS_A/B` に加えて,
最大Rank 10まで `RANK_01_BANDPASS_A/B`, `RANK_02_BANDPASS_A/B` ... の形式で係数を表示する.
別Rankを試したい場合は, そのRankの `A/B` を `DEFAULT_BANDPASS_A/B` に貼り替える.

一時的にコマンドラインで指定する場合:

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --filter-a "1,-3.97,5.93,-3.94,0.98" --filter-b "0.000039,0,-0.000078,0,0.000039"
```

IIRノッチを使う場合は `design_iir_notch_filter.py` の
`DEFAULT_NOTCH_A`, `DEFAULT_NOTCH_B` 形式の出力を,
`analysis/PhaseTiming.py` または `analysis/Wavelet.py` 冒頭の
`DEFAULT_NOTCH_A/B` へ貼る.
実行時は `--notch on` で有効化する. 既定はOFF.

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --notch on
python analysis\Wavelet.py max2_parallel_20260520_185539 --notch on
```

## check_filter.py

### 目的

既にある係数がどういうフィルタか確認する.

表示内容:

| グラフ | 内容 |
|---|---|
| Frequency response | dB軸と振幅倍率軸で周波数特性を見る |
| Group delay | 周波数ごとの遅延を見る |
| Pole-zero plot | 極と零点を見る |
| Time waveform | 入力テスト信号とフィルタ出力を比較 |

### JSONから確認

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1
```

Rank 3を見る:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 3
```

### Pythonファイルから確認

`analysis/PhaseTiming.py` の `DEFAULT_BANDPASS_A/B` を確認:

```powershell
python filter_design\check_filter.py analysis\PhaseTiming.py
```

係数名を変えている場合:

```powershell
python filter_design\check_filter.py --module analysis\PhaseTiming.py --a-name DEFAULT_BANDPASS_A --b-name DEFAULT_BANDPASS_B
```

### 係数を直接指定

```powershell
python filter_design\check_filter.py --a "1,-3.97,5.93,-3.94,0.98" --b "0.000039,0,-0.000078,0,0.000039"
```

### テスト信号を変える

10 Hz, 7 Hz, 20 Hzを混ぜて入力する:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --test-freqs 10,7,20 --test-amps 1,0.5,0.5
```

グラフ範囲:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --plot-max-freq 60
```

群遅延の縦軸:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --delay-ylim 0,1000
```

画像保存:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --save-figure filter_design\check_rank1.png
```

情報保存:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --save-info filter_design\check_rank1.txt
```

表示せず保存:

```powershell
python filter_design\check_filter.py filter_design\filter_10hz.json --rank 1 --save-figure filter_design\check_rank1.png --no-show
```

## 用語

### dBと振幅倍率

振幅倍率は以下で計算する.

```text
振幅倍率 = 10^(dB / 20)
```

例:

| dB | 振幅倍率 |
|---:|---:|
| `0 dB` | `1.0倍` |
| `-3 dB` | `約0.707倍` |
| `-6 dB` | `約0.5倍` |
| `+3 dB` | `約1.414倍` |
| `-20 dB` | `0.1倍` |

### Q値

Qは鋭さの指標.

```text
Q = target周波数 / -3dB帯域幅
```

Qが高いほど狭い範囲だけを通す. ただし, 一般に群遅延が増えやすく, 極が単位円に近くなりやすい.

### 群遅延

周波数成分がフィルタを通ったときの遅れ. 位相タイミングを見る解析では重要.

`design_peak_filter.py` では `--max-target-delay-ms` でtarget周波数とその周辺の遅延が大きい候補を除外できる.
既定では `--target-delay-neighborhood-width 1.0` なので, `target_freq ± 1.0 Hz` の最大絶対群遅延も同じ上限で判定する.
target一点だけで判定したい場合は `--target-delay-neighborhood-width 0` を指定する.

### 極と安定性

IIRフィルタでは極が単位円内にあれば理論上安定.

ただし, 極が1に近いほど鋭いフィルタになりやすい反面, 過渡応答が長くなり, 数値誤差にも弱くなる.

## よくある流れ

10 Hz計測データを位相タイミング解析したい場合:

1. フィルタ候補を探索.

```powershell
python filter_design\design_peak_filter.py 10 --output filter_design\filter_10hz.json
```

2. グラフ左側の `R1`, `R2` で候補を見比べる.
3. 良い候補の係数を `analysis/PhaseTiming.py` の `DEFAULT_BANDPASS_A/B` に貼る.
4. 必要なら `check_filter.py` で再確認.

```powershell
python filter_design\check_filter.py analysis\PhaseTiming.py --target-freq 10
```

5. PhaseTimingを実行.

```powershell
python analysis\PhaseTiming.py max2_parallel_20260520_185539 --frequency 10 --phases stimulus
```

## トラブルシュート

### scipyが見つからない

```powershell
conda env update -f environment.yml --prune
conda activate eeg-max2
```

### 候補が出ない

条件が厳しすぎる可能性がある.

```powershell
python filter_design\design_peak_filter.py 10 --max-target-delay-ms 0 --max-q 0 --max-target-gain-db 0 --max-near-target-gain-db 0
```

周辺の群遅延だけが厳しい場合は, `--target-delay-neighborhood-width 0` でtarget一点のみの確認に戻せる.
短時間応答ゲインだけが厳しい場合は, `--min-time-response-gain-db -6` のように緩めるか,
`--no-time-response-gain-check` を指定する.

### Rankが似て見える

係数がほぼ同じ候補は重複除去しているが, 設定が近い候補は似た形になりやすい. 左側の `R1`, `R2` チェックで重ね表示を整理すると見やすい.

### 総当たり探索が遅い

`--search-preset sharp` や `--search-preset exhaustive` は探索数が増える.
短く確認したい場合は `--no-plot --no-check-filter --no-progress` を付ける.
さらに絞る場合は `--families cheby2` や `--passband-offset-values 0.02,0.05,0.1,0.2` のように候補を減らす.

### 群遅延が大きい

狭帯域で鋭いフィルタほど遅延が増えやすい. `--max-target-delay-ms` を下げるか, `--max-q` を下げる.

### 極が1に近い

`--max-pole-abs` を下げる.

```powershell
python filter_design\design_peak_filter.py 10 --max-pole-abs 0.999
```
