# EEG26対応の調査・検証報告

検証日: 2026-10-03。変更は26_EEG内のみ。commit/pushなし。両ファームウェアの変更・flash書き換えなし。

## 調査結果

- 旧装置の現行 `hs_EEG_board/Core/Src/main.c`: `MEAS_CH_NUM=3`、CONFIG1 `0x94`（1 kSPS）、signed raw codeをCSVで出力。READMEには8ch等の古い記述があり、既存PCの1ch/3ch受信動作を優先した。
- 新型現行 `Core/Src/eeg_app.c`: protocol 2、68 byte、54 byte ADS payload、uint32 LE sequence・1 MHz DRDY timestamp、CRC-16/CCITT-FALSE（BE）。ローカルソースのGit blob SHA `79a03cc7b73684e1cbc3bb60d938c0afa6beac67` はGitHub現行ファイルと一致。
- `ads1299.c`: EEG既定CONFIG1はD6（250 SPS）。ADS1/2の各3 byte statusを飛ばして全16chをsigned 24-bitで復号。USB CDC line codingは実速度を決めない。
- Evaluation/Pythonのrealtime_fft.pyとhardware_smoke_test.pyはv1のsyncを使用していた。コピーせずCソースと実機v2を基準に実装した。
- 既存計測はserialプロセスと表示プロセスが共有メモリとEventで協調し、PCのperf_counter_nsで同期。表示・events・framesの処理は共通のまま維持した。
- 元のチェックアウトにはrepeat.pyとPhaseTiming_ch1_minus_ch2.pyが存在せず、新規追加した。

## 実機ポート・コマンド

- device: `COM4`
- description: USB シリアル デバイス (COM4)
- hwid: `USB VID:PID=0483:5740 SER=356134833233 LOCATION=1-2`

STOP: `STOPPED sequence=0 dropped=0`、HELP: コマンド一覧、STATUS: `timebase=1000000Hz protocol=2` を確認。
初期化: STOP → STATUS → MODE EEG → RREG 1/2 01 01 → 指定時WREG 1/2 01 D4（1000 SPS）→ RREGで両ADS確認 → FORMAT BINARY → START。
既定250 SPSではWREGせずD6の読み戻しを採用。毎回終了時にSTOPPED応答を確認してポートをclose。

## 実機取得結果

実際のGLFW刺激表示とserial_workerで取得。CSV、metadata、events、framesを保存。
取得秒数とframe/sはPCのSTART応答後から受信loop終了までの測定で、STOP時のflush分も保存frame数に含む。

| 保存フォルダ（measurement/measurement_data内） | 設定SPS | 取得秒数 | frames | frame/s | CRC | sequence gaps | firmware dropped | STOP応答 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| eeg26_parallel_20261003_165314 | 1000 | 4.968639 | 4957 | 997.657 | 0 | 0 | 9 | True |
| eeg26_parallel_20261003_165719 | 250 | 2.969141 | 737 | 248.220 | 0 | 0 | 2 | True |
| eeg26_parallel_20261003_170053 | 1000 | 2.937911 | 2926 | 995.946 | 0 | 0 | 9 | True |
| eeg26_parallel_20261003_170059 | 1000 | 2.963327 | 2952 | 996.178 | 0 | 0 | 9 | True |

repeat経由の最後の2件は対話なし連続実行を確認。timestamp gap eventsは両方0、partial_frame_bytesは0。
全取得で16chすべて復号・保存でき、全chに変動があり、±full-scale値は0件。
最初の約5秒取得のraw範囲は次のとおり。

| ch | min | max | 異なる値の数 |
| --- | ---: | ---: | ---: |
| ch1 | 158668 | 161839 | 1325 |
| ch2 | 158308 | 161414 | 1301 |
| ch3 | 160181 | 163201 | 1281 |
| ch4 | 162113 | 165137 | 1266 |
| ch5 | 303661 | 701003 | 4924 |
| ch6 | 296129 | 564815 | 4884 |
| ch7 | 286754 | 655968 | 4918 |
| ch8 | 270582 | 411834 | 4774 |
| ch9 | 3694678 | 5051886 | 4950 |
| ch10 | 3572384 | 4884889 | 4951 |
| ch11 | 2997795 | 4675006 | 4957 |
| ch12 | 2987819 | 4188830 | 4942 |
| ch13 | 4345151 | 5196694 | 4943 |
| ch14 | 4340751 | 5197942 | 4947 |
| ch15 | 3133652 | 3642346 | 4925 |
| ch16 | 2861396 | 3470757 | 4923 |

## 自動テスト・解析確認

- `python -m unittest discover -s tests -v`: **28 tests passed**。
- parser: 正常68 byte、2frame連結、全67分割位置、garbage/split sync、CRC破損、重なったsyncと不正headerからの再同期、sequence gap、signed24境界4種、version/length不正、sequence/time wrap、buffer上限、部分frame。
- CRC check vector `123456789` → `0x29B1`。Cのpoly=0x1021、init=FFFF、no-reflect/no-xorと一致。
- transport: ERR/MISMATCH中止、ACK timeout、START応答時にbinaryを消費しないこと、STOP flush、250/1000 SPS読み戻し、timestamp欠落監視。
- worker: prepare/START/read例外とキャンセルでSTOPとcloseを確認、legacy CSV header・値・parse_error行を確認。
- analysis: 動的16ch、存在しないchのエラー、USBまとめ受信でも1000Hzを使用、10Hz合成信号のFFT、旧データのPC時刻保持とchごとのsample rate推定維持。
- `python -m compileall -q measurement analysis tests`: 成功。`git diff --check`: 成功。
- 旧実データ2件（max2_parallel_20260520_185539 / 191332）は変更前HEADのFFT/Wavelet/PhaseTimingと比較し、各chの読み込み時刻・値・均一再サンプリング配列とfsが完全一致。
- 旧実データ185539でFFT/Wavelet/PhaseTiming CLIが成功。
- 新実データ250 SPSでFFT/Wavelet/PhaseTiming（--no-filter）の全16ch自動検出が成功。
- 新実データ1000 SPSでch1,ch2,ch8,ch16指定の3解析とch1-ch2差分が成功。PhaseTimingの既定BPF使用も成功。

## 旧装置互換性

COM番号/名前選択、115200 baud、readline、ウォームアップ、1ch/3ch解釈、CSV header/列順/parse_error/raw_line、max2_parallelフォルダ、max2_summary作成を維持。
旧装置へのSTART/STOP等の追加コマンド送信なし。旧metadataにdevice情報がない場合は従来のPC時刻とfs推定を使用する。
装置未指定時だけ新しい装置選択が入る。従来の非対話運用には `--device legacy` を追加する。

## 残る問題・未検証事項

- **全試験でfirmware droppedが非zero。CRCとsequenceは0でも装置内の変換取得欠落なしは達成していない。** 現行Cではpending>1やSPI失敗でdroppedだけ増え、sequenceは送信sample時に進む。最終2件のtimestamp gap eventsは0なので、欠落発生位置を保存frameだけから特定できない。原因・発生位置は未確定。ファームウェア変更は行わなかった。
- timestampとsequenceのmonitorは通信欠落と内部欠落を完全には同一視しない。解析の線形補間は欠落を復元するものではない。
- 16chの値にはDC offset/driftがあり、生理信号としての品質・校正・電極状態は未評価。ソフトウェア復号と保存の確認である。
- 実機で検証した速度は250/1000 SPSのみ。長時間取得、他のsample rate、旧装置の実機計測は未検証。
- 実機を物理切断した試験やESC/window closeの操作試験は未実施。キャンセル・例外時のSTOP/closeは自動テストで確認。
- PC刺激とdevice時計はハードウェア同期ではなく、初回PC受信にanchorしたdevice相対時刻にはUSB遅延offsetが残る。厳密な同期精度は未検証。
- PhaseTimingの既定BPFは1000 Hz用。250等では--no-filterまたは該当fsで設計した係数を指定する。

## 変更ファイル

- measurement/offline_max2_parallel_measurement.py（共通worker、装置CLI、metadata、終了処理）
- measurement/device_backends.py（新規：旧/新バックエンド、protocol/parser/commands）
- measurement/repeat.py（新規）
- analysis/FFT.py、analysis/Wavelet.py、analysis/PhaseTiming.py
- analysis/serial_data.py（新規：ch検出、metadata fs、device時刻）
- analysis/PhaseTiming_ch1_minus_ch2.py（新規）
- tests/test_device_backends.py、tests/test_acquisition_cleanup.py、tests/test_serial_analysis.py（新規）
- README.md、EEG26_VALIDATION.md（本報告）
- 上記4件の実機出力フォルダ（各metadata/events/frames/serial_samples.csv）

最終確認: STATUS state=2 mode=1（READY）、sequence=2952 dropped=9。追加STOPにもSTOPPED応答あり。実機は停止状態。

## PC側初期設定方式への追補

新型はsample-rate省略時もPCの既定250 SPSをWREGで両ADSへ設定する方式に変更。旧装置は従来どおり。
自動テスト28件成功。firmware既定を500 SPSとして模擬してもPC既定250 SPSで上書き・読み戻し成功を確認。
COM4で2秒の実機確認: WREG 1/2 01 D6、sample rate=250 SPS、frames=497、CRC=0、sequence gaps=0、firmware dropped=2、STOP応答あり。
この追補の取得はバックエンド単体で行い、追加の刺激表示・CSV保存は行っていない。前述の既存試験結果は変更前方式の履歴として保持する。

## 接続中実機での再試験

[EEG26_HARDWARE_RETEST.md](EEG26_HARDWARE_RETEST.md) に最新結果を記録。1000 SPSで10秒の刺激並行取得と全7設定の3秒試験を実施。250〜8000 SPSの設定・取得を確認したが内部droppedは残る。16000 SPSは実測約8014 frame/s、欠落・飽和が多く正常取得とは扱わない。

## 対応上限の確定

ユーザー指定により、新型装置の動作保証範囲の上限は8000 Hz。PC側の指定可能値を250/500/1000/2000/4000/8000 SPSに制限。16000 SPSはCLIとバックエンドで拒否する。上記16000 SPSの結果は対応範囲外で行った過去試験の記録として保持。今回の変更では追加の実機取得は行わず、自動テスト30件成功（8000の設定、16000の拒否を含む）。
