# hardware README

MAX2/EEG計測で使うハードウェア, シリアル通信, 実験前チェックのメモ.

詳細な基板情報は以下を参照.

https://github.com/bdaad/hs_EEG_board

## ファイル説明

| ファイル | 内容 |
| :--- | :--- |
| [hardware_README.md](hardware_README.md) | ハードウェア, COMポート, 実験前チェックの説明 |

## 目次

- [目的](#目的)
- [実験前チェック](#実験前チェック)
- [COMポート確認](#comポート確認)
- [シリアル設定](#シリアル設定)
- [受信データ形式](#受信データ形式)
- [計測中の保存内容](#計測中の保存内容)
- [同期に関する注意](#同期に関する注意)
- [トラブルシュート](#トラブルシュート)
- [関連コマンド](#関連コマンド)

## 目的

このプロジェクトでは, MAX2側からPCへシリアル通信で計測値を送り, PC側で点滅刺激の表示ログと同時に保存する.

PC側の計測コードは以下.

```text
measurement/offline_max2_parallel_measurement.py
```

## 実験前チェック

| 項目 | 確認内容 |
| :--- | :--- |
| USB接続 | MAX2がPCに接続されている |
| COMポート | デバイスマネージャまたは `--list-ports` で認識されている |
| ボーレート | MAX2側とPC側の設定が一致している |
| データ形式 | 1chまたは3chの数値行が送信されている |
| 電極 | 接触, GND, 参照電極が安定している |
| ノイズ | 50/60Hz電源ノイズやケーブルの揺れを確認 |
| モニター | 刺激表示に使う画面とリフレッシュレートを確認 |
| 明るさ | 点滅刺激が強すぎない設定にする |

## COMポート確認

使用可能なポート一覧を表示.

```powershell
python measurement\offline_max2_parallel_measurement.py --list-ports
```

番号で指定.

```powershell
python measurement\offline_max2_parallel_measurement.py --com 1
```

COM名で指定.

```powershell
python measurement\offline_max2_parallel_measurement.py --com COM3
```

`DEFAULT_COM_PORT` が `None` の場合, 起動時に一覧から番号選択できる.

## シリアル設定

主な初期値.

| 設定 | 初期値 | 内容 |
| :--- | :--- | :--- |
| `DEFAULT_BAUDRATE` | `115200` | シリアル通信速度 |
| `DEFAULT_CHANNEL_MODE` | `auto` | 1ch/3chを自動判定 |
| `DEFAULT_SERIAL_TIMEOUT_SEC` | `0.001` | シリアル読み取り待ち時間 |
| `DEFAULT_SERIAL_WARMUP_SEC` | `1.0` | 接続直後に読み捨てる時間 |
| `DEFAULT_READY_TIMEOUT_SEC` | `15.0` | シリアル準備完了待ちの最大時間 |

接続直後は値が不安定になることがあるため, `serial_warmup_sec` の間は受信データを保存しない.

一時的に変更する例.

```powershell
python measurement\offline_max2_parallel_measurement.py --com 1 --baudrate 115200 --serial-warmup-sec 1.0
```

## 受信データ形式

`channel-mode auto` では, 1個または3個の数値を含む行を受け付ける.

1ch例.

```text
12345
```

3ch例.

```text
12345,12400,12280
```

解析では `serial_samples.csv` の `ch1`, `ch2`, `ch3` を使う.
パースできない行は `parse_error` に理由が記録される.

## 計測中の保存内容

| ファイル | 内容 |
| :--- | :--- |
| `serial_samples.csv` | 受信値, 受信時刻, フェイズ名, パース結果 |
| `frames.csv` | 表示フレームごとの時刻, フェイズ, 刺激ON/OFF |
| `events.csv` | フェイズ開始, 終了イベント |
| `metadata.json` | 実験条件, シリアル設定 |
| `max2_summary.csv` | チャンネルごとの概要統計 |

`frames.csv` は刺激のON/OFFタイミング確認に使う.
`serial_samples.csv` はMAX2からPCへ届いた時刻をPC側で記録したもの.

## 同期に関する注意

PC側では, シリアル受信時刻とフレーム表示時刻を同じ `perf_counter_ns` 系の時計で記録する.
そのためPC内部での比較はしやすい.

ただし, MAX2のAD変換タイミングと画面の実発光タイミングがハードウェア同期しているわけではない.
厳密な刺激同期が必要な場合は, フォトセンサや外部トリガで確認する.

## トラブルシュート

| 症状 | 対応 |
| :--- | :--- |
| COMポートが出ない | USB抜き差し, デバイスマネージャ, ドライバ確認 |
| 受信値が空 | MAX2側の送信開始, ボーレート, COM番号を確認 |
| `parse_error` が多い | 区切り文字, 改行, 1ch/3ch形式を確認 |
| 値が大きく乱れる | 電極接触, ケーブル揺れ, GND, 電源ノイズを確認 |
| 刺激が別画面に出る | `--monitor-index` を変更 |
| フルスクリーンが困る | `--windowed` を付ける |

## 関連コマンド

```powershell
python measurement\offline_max2_parallel_measurement.py --list-ports
python measurement\offline_max2_parallel_measurement.py --com 1 --windowed
python analysis\FFT.py max2_parallel_20260520_185539
python analysis\PhaseTiming.py max2_parallel_20260520_185539
```
