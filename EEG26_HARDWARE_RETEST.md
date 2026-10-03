# 新型EEG実機再試験

2026-10-03、COM4（USB シリアル デバイス、VID:PID 0483:5740）で実施。
コード変更・firmware書き換え・commit/pushなし。

## 刺激表示と1000 SPS計測

実行: `python measurement/offline_max2_parallel_measurement.py --device eeg26 --com COM4 --sample-rate 1000 --pre-sec 2 --stim-sec 6 --post-sec 2 --windowed --window-width 640 --window-height 480`

- 両ADSへWREG 01 D4送信、RREG D4読み戻し一致、metadataの設定値1000 SPS。
- PC受信loop時間: 9.958919秒（予定の刺激実験は10秒）。device時刻のsample間隔合計: 9.992222秒。
- 保存frames: 9946、実測 998.703 frame/s（STOP flush分を含む）。
- CRC errors=0、sequence gaps=0、partial bytes=0。
- firmware dropped=9、timestamp gap events=1、最大間隔2009 µs。
- 異常間隔は最初のsampleから2番目（sequence 1→2）の2009 µs。開始直後で観測した事実であり、dropped=9の全原因・全発生位置は未確定。
- 16chすべてに変動あり、全chの±full-scale値は0件。physiological signalの品質・校正は未評価。
- serial_samples.csvの行数とreceived_framesが一致。metadata/events/frames/serial_samplesを保存。frames.csvは600行。
- FFT/Wavelet/PhaseTimingをch1,ch2,ch8,ch16で実行成功（PhaseTimingは--no-filter）。解析はmetadataの1000 SPSを使用。
- STOPPED応答あり、ポートclose。

保存先: `measurement/measurement_data/eeg26_parallel_20261003_171816/`

## 全sample rateの設定・取得試験

各3秒のバックエンド単体試験。各設定でWREGとRREGの一致確認後にSTARTし、16chを復号、STOPPED応答を確認。
この試験は刺激表示や追加CSV保存を行わず、統計・ch別min/max・飽和数・コマンド応答をresults.jsonへ記録。

| 設定SPS | 取得秒数 | frames | 実測frame/s | CRC | sequence gap | timestamp gapイベント | firmware dropped | 飽和sample値の件数（全ch合計） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 250 | 3.002470 | 745 | 248.129 | 0 | 0 | 0 | 2 | 0 |
| 500 | 3.010588 | 1498 | 497.577 | 0 | 0 | 0 | 4 | 0 |
| 1000 | 3.001322 | 2989 | 995.894 | 0 | 0 | 0 | 9 | 0 |
| 2000 | 3.000320 | 5979 | 1992.788 | 0 | 0 | 1 | 18 | 0 |
| 4000 | 3.001166 | 11965 | 3986.784 | 0 | 0 | 0 | 37 | 0 |
| 8000 | 3.000437 | 23930 | 7975.506 | 0 | 0 | 0 | 73 | 0 |
| 16000 | 3.000162 | 24043 | 8013.900 | 0 | 0 | 23719 | 3705 | 1130 |

全設定で両ADSのrate読み戻しが一致し、全16chに変動があり、STOP応答を確認。
250〜8000 SPSは公称に近いframe/sで取得。ただし全rateでfirmware droppedは非zeroで、欠落なしとは判断できない。
16000 SPSは約8014 frame/sにとどまり、timestamp gapイベント23719件、firmware dropped3705、飽和値1130件。
**16000 SPSの正常な16ch取得は確認できなかった。現在の実機・firmwareでは使用可能とみなさない。**
CRC/sequence gap=0はADS変換の取得完全性を保証しない。欠落の根本原因は未確定。

詳細JSON: `measurement/measurement_data/eeg26_hardware_check_20261003_171901/results.json`

試験終了後、MODE EEGとWREG/RREGで1000 SPSへ戻し、STOPPED応答を再確認。実機は停止状態。
長時間測定・旧装置実機・人体信号品質・ハードウェア同期精度はこの再試験の対象外。

## 対応上限の確定

ユーザー指定により、新型装置の動作保証範囲の上限は8000 Hz。PC側の指定可能値を250/500/1000/2000/4000/8000 SPSに制限。16000 SPSはCLIとバックエンドで拒否する。上記16000 SPSの結果は対応範囲外で行った過去試験の記録として保持。今回の変更では追加の実機取得は行わず、自動テスト30件成功（8000の設定、16000の拒否を含む）。
