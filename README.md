# オフラインMAX2計測スクリプト README

`offline_max2_parallel_measurement.py` は、SSVEP/EEG計測用のオフライン計測スクリプトです。  
画面中央に注視点を表示し、その後、画面中央に1つの点滅刺激を表示し、最後に再び注視点を表示します。表示フレームとシリアル受信サンプルをCSVに保存し、後からオフライン解析できる形にします。

## 基本シーケンス

デフォルトの流れは次の通りです。

1. 注視点を10秒表示
2. 中央の点滅刺激を3秒表示
3. 注視点を10秒表示

刺激は画像ファイルではなく、OpenGLで白い円を直接描画します。背景は黒です。画像ファイルのパス依存を避けるため、`circle.png` や `look_point.png` は不要です。

## 実行環境

想定実行環境はWindowsです。

必要な主なライブラリ:

```bash
pip install pyserial glfw PyOpenGL numpy psutil
```

`psutil` はWindowsでプロセス優先度を上げるために使います。入っていない場合でもスクリプトは優先度変更をスキップして続行します。

### condaで一括インストールする

同じフォルダに `environment.yml` を用意しています。

新しい環境を作る場合:

```bash
conda env create -f environment.yml
conda activate eeg-max2
```

すでに作成済みの環境を更新する場合:

```bash
conda env update -f environment.yml --prune
conda activate eeg-max2
```

インストール後の確認:

```bash
python -c "import serial, glfw, OpenGL, numpy, psutil; print('ok')"
```

このスクリプトで使う外部ライブラリとcondaパッケージ名:

| import名 | condaパッケージ |
|---|---|
| `serial` | `pyserial` |
| `glfw` | `pyglfw` |
| `OpenGL` | `pyopengl` |
| `numpy` | `numpy` |
| `psutil` | `psutil` |

Macでも依存ライブラリが揃えば描画自体は動く可能性がありますが、デフォルトのCOMポートはWindows用です。Macでは `/dev/cu.usbserial-...` のようなポート指定が必要です。

## 最初にCOMポートを確認する

```bash
python offline_max2_parallel_measurement.py --list-ports
```

表示されたポート名を `--com` に指定します。

例:

```bash
python offline_max2_parallel_measurement.py --com COM8
```

## 基本実行

```bash
python offline_max2_parallel_measurement.py --com COM8
```

デフォルト条件:

| 項目 | デフォルト |
|---|---:|
| シリアルポート | `COM8` |
| ボーレート | `115200` |
| チャンネル形式 | `auto` |
| 刺激前注視点 | `10秒` |
| 点滅刺激 | `3秒` |
| 刺激後注視点 | `10秒` |
| 点滅周波数 | `7.5Hz` |
| 点滅開始状態 | `点灯開始` |
| 注視点半径 | `0.025` |
| 刺激半径 | `0.22` |
| 表示 | フルスクリーン |

## 実験条件の設定

### チャンネル設定

シリアル入力は1chと3chの両方に対応しています。

対応形式:

```text
ch1
ch1
ch1
```

または

```text
ch1,ch2,ch3
ch1,ch2,ch3
ch1,ch2,ch3
```

オプション:

```bash
--channel-mode auto
```

`auto` は1ch/3chを自動判定します。通常はこれで実行してください。

```bash
--channel-mode one
```

1chのみとして読みます。

```bash
--channel-mode three
```

3ch入力を必須として読みます。3つ未満の値しか来ない行は `parse_error` に記録されます。

### 表示時間

```bash
--pre-sec 10
--stim-sec 3
--post-sec 10
```

意味:

| オプション | 意味 |
|---|---|
| `--pre-sec` | 点滅刺激前の注視点表示時間 |
| `--stim-sec` | 点滅刺激の表示時間 |
| `--post-sec` | 点滅刺激後の注視点表示時間 |

例:

```bash
python offline_max2_parallel_measurement.py --com COM8 --pre-sec 5 --stim-sec 4 --post-sec 5
```

### 点滅回数で指定する

秒数ではなく、点滅周期数で指定したい場合は `--stim-cycles` を使います。

```bash
python offline_max2_parallel_measurement.py --com COM8 --frequency 7.5 --stim-cycles 30
```

この場合、刺激時間は次の式で決まります。

```text
刺激時間 = 点滅周期数 / 周波数
```

例:

```text
30周期 / 7.5Hz = 4秒
```

`--stim-cycles` を指定した場合、`--stim-sec` より優先されます。

ここでの1周期は、点灯と消灯を含む1回の完全な周期です。

### 点滅周波数

```bash
--frequency 7.5
```

例:

```bash
python offline_max2_parallel_measurement.py --com COM8 --frequency 10
```

60Hz画面では、代表例として次のようになります。

| 周波数 | 1周期のフレーム数 | 半周期のフレーム数 |
|---:|---:|---:|
| 7.5Hz | 8フレーム | 4フレーム |
| 10Hz | 6フレーム | 3フレーム |

画面の実リフレッシュレートはGLFWから取得します。必要なら `--refresh-rate` で計算上のリフレッシュレートを明示できます。ただし、実際の画面リフレッシュレートと一致させてください。

```bash
python offline_max2_parallel_measurement.py --com COM8 --refresh-rate 60
```

### 点滅開始状態

```bash
--stimulus-start on
```

点灯から開始します。

```bash
--stimulus-start off
```

消灯から開始します。

例:

```bash
python offline_max2_parallel_measurement.py --com COM8 --stimulus-start off
```

### サイズ設定

```bash
--fixation-radius 0.025
--stimulus-radius 0.22
```

どちらもOpenGLの正規化座標系に基づく半径です。ピクセル単位ではありません。

例:

```bash
python offline_max2_parallel_measurement.py --com COM8 --stimulus-radius 0.25
```

### 点滅中にも注視点を重ねる

通常、点滅刺激中は刺激円だけを表示します。点滅中にも小さい注視点を中央に重ねたい場合は次を指定します。

```bash
python offline_max2_parallel_measurement.py --com COM8 --show-fixation-during-stimulus
```

## ウィンドウ設定

通常はフルスクリーンで表示します。

ウィンドウ表示で確認したい場合:

```bash
python offline_max2_parallel_measurement.py --com COM8 --windowed
```

ウィンドウサイズ指定:

```bash
python offline_max2_parallel_measurement.py --com COM8 --windowed --window-width 1280 --window-height 720
```

複数モニターがある場合:

```bash
python offline_max2_parallel_measurement.py --com COM8 --monitor-index 1
```

## 出力先

デフォルトでは、スクリプトと同じフォルダ内の `measurement_data` に保存します。

```text
measurement_data/max2_parallel_YYYYMMDD_HHMMSS/
```

出力先を指定する場合:

```bash
python offline_max2_parallel_measurement.py --com COM8 --output-dir ./measurement_data_test
```

## 出力ファイル

1回の実行で次のファイルが作られます。

```text
metadata.json
events.csv
frames.csv
serial_samples.csv
max2_summary.csv
```

### metadata.json

実行条件を保存します。

主な内容:

| 項目 | 内容 |
|---|---|
| `created_at` | 実行日時 |
| `script` | 実行スクリプト名 |
| `config.com_port` | COMポート |
| `config.baudrate` | ボーレート |
| `config.channel_mode` | 1ch/3ch設定 |
| `config.pre_fixation_sec` | 刺激前注視点秒数 |
| `config.stimulus_sec` | 実際に使われた刺激秒数 |
| `config.stimulus_cycles` | 周期数指定。未指定なら `null` |
| `config.post_fixation_sec` | 刺激後注視点秒数 |
| `config.stimuli` | 刺激周波数、位置、サイズ、点灯開始/消灯開始 |
| `files` | 各出力CSVのパス |

### events.csv

実験イベントの切り替わりを記録します。

列:

| 列名 | 内容 |
|---|---|
| `event_index` | イベント番号 |
| `event_name` | イベント名 |
| `phase_code` | 区間番号 |
| `phase_name` | 区間名 |
| `frame_index` | その時点のフレーム番号 |
| `time_ns` | PC時刻。`time.perf_counter_ns()` |
| `experiment_time_s` | 実験開始からの秒数 |

イベント例:

```text
experiment_start
fixation_before_start
fixation_before_end
stimulus_start
stimulus_end
fixation_after_start
fixation_after_end
experiment_end
```

### frames.csv

画面フレームごとの表示状態を記録します。`glfw.swap_buffers()` が戻った直後の時刻を保存します。

列:

| 列名 | 内容 |
|---|---|
| `frame_index` | 全体のフレーム番号 |
| `phase_code` | 区間番号 |
| `phase_name` | 区間名 |
| `phase_frame_index` | その区間内でのフレーム番号 |
| `present_time_ns` | VSync後のフレーム提示時刻 |
| `experiment_time_s` | 実験開始からの秒数 |
| `stimulus_active` | 点滅刺激区間なら `1` |
| `stimulus_on_mask` | 刺激ON/OFF状態のビットマスク |
| `stimulus_center_7_5hz_on` | デフォルト刺激のON/OFF。条件により名前は変わります |

刺激がONなら `1`、OFFなら `0` です。

### serial_samples.csv

シリアルから受け取った全サンプルを保存します。

列:

| 列名 | 内容 |
|---|---|
| `sample_index` | 受信サンプル番号 |
| `pc_time_ns` | シリアル行を読んだPC時刻 |
| `experiment_time_s` | 実験開始からの秒数 |
| `phase_code` | その時点の区間番号 |
| `phase_name` | その時点の区間名 |
| `frame_index` | 直近の表示フレーム番号 |
| `frame_time_ns` | 直近の表示フレーム時刻 |
| `stimulus_active` | 点滅刺激区間なら `1` |
| `stimulus_on_mask` | 直近フレームの刺激ON/OFF状態 |
| `serial_channel_count` | 読み取れたチャンネル数 |
| `ch1` | 1ch目の値 |
| `ch2` | 2ch目の値。1ch入力なら空 |
| `ch3` | 3ch目の値。1ch入力なら空 |
| `parse_error` | パース失敗理由。正常時は空 |
| `raw_line` | 受信した元の文字列 |

### max2_summary.csv

`serial_samples.csv` から作る簡易サマリです。区間ごと、チャンネルごとに、局所最大値の上位2個と局所最小値の上位2個を保存します。

列:

| 列名 | 内容 |
|---|---|
| `phase_name` | 区間名 |
| `channel` | `ch1`, `ch2`, `ch3` |
| `kind` | `max` または `min` |
| `rank` | 1位または2位 |
| `sample_index` | 元サンプル番号 |
| `experiment_time_s` | 実験開始からの秒数 |
| `value` | 値 |

このファイルを作らない場合:

```bash
python offline_max2_parallel_measurement.py --com COM8 --skip-max2-summary
```

## 同期の考え方

このスクリプトでは、表示プロセスとシリアル受信プロセスを分けています。

表示側:

1. GLFW/OpenGLで描画
2. `glfw.swap_interval(1)` によりVSync有効
3. `glfw.swap_buffers()` 後にフレーム提示時刻を `frames.csv` に保存
4. 共有状態に現在のフレーム番号、区間、刺激ON/OFFを保存

シリアル側:

1. 別プロセスで常時 `ser.readline()` を実行
2. 1行受信した時点の `time.perf_counter_ns()` を保存
3. その時点で共有されている直近フレーム状態を一緒に `serial_samples.csv` に保存

つまり、表示フレームとシリアルサンプルは同じPCクロック上の時刻で対応付けます。  
ただし、PCだけで完全なハードウェア同期を保証するものではありません。デバイス側のサンプリング開始と画面提示を物理的に同期したい場合は、外部トリガやデバイス側タイムスタンプが別途必要です。

## 実行例

### デフォルト条件

```bash
python offline_max2_parallel_measurement.py --com COM8
```

### 3ch入力、7.5Hz、消灯開始

```bash
python offline_max2_parallel_measurement.py --com COM8 --channel-mode three --frequency 7.5 --stimulus-start off
```

### 7.5Hzを30周期表示

```bash
python offline_max2_parallel_measurement.py --com COM8 --frequency 7.5 --stim-cycles 30
```

### 10Hzを3秒表示

```bash
python offline_max2_parallel_measurement.py --com COM8 --frequency 10 --stim-sec 3
```

### 注視点5秒、刺激4秒、注視点5秒

```bash
python offline_max2_parallel_measurement.py --com COM8 --pre-sec 5 --stim-sec 4 --post-sec 5
```

### ウィンドウ表示で確認

```bash
python offline_max2_parallel_measurement.py --com COM8 --windowed --window-width 1280 --window-height 720
```

## 終了方法

実験は指定されたシーケンスが終わると自動終了します。途中終了したい場合は `ESC` キーを押します。

## トラブルシュート

### COMポートが開けない

`--list-ports` でポート名を確認してください。

```bash
python offline_max2_parallel_measurement.py --list-ports
```

他のソフトが同じCOMポートを開いている場合は閉じてください。

### `parse_error` が出る

`serial_samples.csv` の `raw_line` を確認してください。

1chの場合:

```text
123
```

3chの場合:

```text
123,456,789
```

余計な文字、空行、チャンネル数不足があると `parse_error` に記録されます。

### 点滅周波数が画面と合わない

使用するモニターのリフレッシュレートを確認してください。  
7.5Hzや10Hzは60Hz画面では整数フレームで表現できます。整数フレームにならない周波数では、スクリプトが警告を出します。

必要なら次のように指定します。

```bash
python offline_max2_parallel_measurement.py --com COM8 --refresh-rate 60
```

### 刺激表示だけ確認したい

現在のスクリプトはシリアル計測前提です。シリアルポートが開けない場合、表示開始前に停止します。表示だけ確認する運用が必要なら、別途 `--no-serial` オプションを追加する想定です。

## 現在の検証範囲

このREADME作成時点で確認したこと:

```bash
python3 -m py_compile offline_max2_parallel_measurement.py
python3 offline_max2_parallel_measurement.py --help
```

また、1ch/3chの文字列パースは簡易確認済みです。  
Windows実機、実COMポート、実ディスプレイでのVSync計測は別途確認してください。
# 26_EEG
