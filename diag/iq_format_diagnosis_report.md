# Airspy IQ Format 진단 보고서

- 날짜: 2026-05-14
- 작업 디렉토리: `/home/user/Thrifty-x`
- 브랜치: `claude/diagnose-airspy-iq-format-BZzTO`
- 산출물: `diag/` 디렉토리 (스크립트, 합성 .card 파일, FFT 플롯, 텍스트 로그 포함)
- 변경 사항: **코드 수정 없음** (진단 전용)

> **UPDATE 2026-05-14 (실데이터 결과 반영)** — 실제 R2 캡처
> (`gs_r2_161_3_20260513_152100_TX2_Gain000/b000/capture.card`) 로 가설을 검증한 결과,
> **시나리오 1-bis (FLOAT32 silent fallback) 는 이 캡처에서 확인되지 않았다**.
> 자세한 내용은 본 보고서 끝의 **§9 Phase E — 실데이터 재검증** 섹션 참조.
> 본문 §1-§5 는 합성 컨트롤 기준 원본 분석을 그대로 보존하며, §9 가 우선한다.

---

## 0. 환경 / 데이터 가용성 노트

- 프롬프트가 명시한 R2 캡처 디렉토리 `~/github/Thrifty-x/example/gs_r2_161_3_20260513_153826` 는
  현재 워크스페이스(`/home/user/Thrifty-x` 및 `~`)에 **존재하지 않음**.
- R2 USB 디바이스 또한 이 환경에서는 연결되어 있지 않음 (`airspy_info` / `lsusb` 결과 없음).
- 따라서 Phase B-1 / B-2 / B-3 의 *실측 데이터* 부분은 수행할 수 없었음. 대신
  - 분석 스크립트(`diag/check_card_format.py`, `diag/check_fft_dualbin.py`,
    `diag/expected_carrier_bin.py`) 를 작성하여 디스크에 저장.
  - 프로젝트 코드(`thriftyx.block_data.complex_to_raw` 등)를 그대로 사용해
    **두 종류의 합성 .card 파일**을 생성하고, 이를 컨트롤로 분석함:
    - `diag/synth_int16_iq.card` — 정상 INT16_IQ 경로의 결과물 (현재 코드가 emit 하는 형식).
    - `diag/synth_float32_misread.card` — 가설 "libairspy 가 FLOAT32_IQ 로 동작했지만
      HAL 이 바이트 스트림을 그대로 디스크에 INT16 로 라벨링했다" 시나리오의 결과물.
- 실제 R2 캡처 파일이 확보되는 즉시 같은 스크립트를 `<path/to/file.card>` 인자로 재실행하면
  본 보고서의 모든 판정 셀이 자동으로 채워진다.

---

## 1. Phase A 결과: 코드 분석

세부 표는 `diag/phase_a_code_analysis.md` 참고. 요약:

| 항목 | 결과 |
|------|------|
| `airspy_set_sample_type` 호출 여부 | **있음** — Python HAL (`airspy_mini.py:361-364`) 과 C fastcapture (`airspy_reader.c:201-202`) 양쪽 모두 `AIRSPY_SAMPLE_INT16_IQ`(=2) 로 호출. |
| 콜백 데이터 dtype | **`int16`** (Python: `np.frombuffer(..., dtype=np.int16)`; C: `int16_t *`) — 일관됨. |
| `raw_to_complex(bit_depth=12)` 정규화 | `floats / 32768.0` (full int16 범위 가정). `airspy_reader.c` 의 주석 *"scaled to full int16 range by libairspy"* 와 일치. |
| `.card` v2 저장 dtype | int16 IQ interleaved, base64. 헤더 `#v2 bit_depth=12 sample_rate=…`. |
| 캡처 경로 | `thriftyx capture` → `_capture_airspy()` (`airspy_capture.py:326`) → Python HAL. **C fastcapture 는 Airspy 경로에서 호출되지 않음.** |
| 표면적 불일치 | **없음**. 세 층(설정, 콜백, 정규화)이 모두 INT16_IQ 를 가정. |

**잠재 약점 1 (강한 후보):** `airspy_mini.py:361-364`
```python
ret = _lib.airspy_set_sample_type(
    self._handle, ctypes.c_int(AIRSPY_SAMPLE_INT16_IQ))
if ret != 0:
    logger.warning("airspy_set_sample_type() failed: %d", ret)
```
실패 시 **경고만 띄우고 진행**한다. libairspy 가 그 디바이스에 대해 sample_type 설정에 실패한 경우
기본값(`AIRSPY_SAMPLE_FLOAT32_IQ` = 0) 으로 데이터가 들어오며, 콜백은 이를 int16 로 reinterpret 한다.
4 byte float32 IQ → 2 byte int16 IQ 로 잘못 잘려서 들어가므로 스펙트럼이 손상된다.

**잠재 약점 2 (약한 후보):** `airspy_set_packing(True)` 호출이 일부 libairspy 빌드에서
sample_type 내부 상태를 리셋한다는 사례가 알려져 있음. 다만 `example/detector_r2.cfg` 에는
`packing` 키가 없으므로 기본값(`False`)이 적용될 가능성이 높아 이 경로는 가능성이 낮다.

**잠재 약점 3 (약한 후보):** R2 LO 누설이 평소보다 큼 → bin 0 근처 DC + 실제 캐리어 bin 의
공존이 "이중 bin" 으로 보일 수 있음. 다만 SNR 이 40→14 dB 로 25 dB 가까이 떨어진 점은
DC 누설만으로 설명되기 어렵다.

---

## 2. Phase B 결과: 데이터 검증 (컨트롤 비교)

스크립트: `diag/check_card_format.py`
로그: `diag/phase_b_card_analysis.txt`

| 메트릭 | INT16_IQ 컨트롤 (`synth_int16_iq.card`) | FLOAT32 misread 컨트롤 (`synth_float32_misread.card`) |
|---|---|---|
| 블록 raw bytes | **262144** | **524288** |
| block_size=65536 가정시 bytes / complex sample | **4.00** ✔ | **8.00** ✗ |
| int16 해석 평균 | -8.49 | -75.48 |
| int16 해석 std | 23160 | 17687 |
| int16 해석 range (-32768..+32767) | PASS | PASS |
| float32 해석 NaN | 있음 (해석 실패) | 없음 |
| float32 해석 range [-2, +2] | FAIL (NaN) | **PASS** (실제 데이터가 float32 였으므로) |
| uint8 해석 centred near 127.4 | PASS (우연) | PASS (우연) |
| 자동 판정 | "INT16_IQ scaled to FULL int16 range" | **"AMBIGUOUS — both interpretations plausible"** |

> 결정적 단서는 **블록 raw 바이트 수**. 정상 경로는 `block_size * 2 (IQ) * 2 bytes(int16) = 262144`.
> 만약 R2 캡처 파일에서 블록당 raw bytes 가 524288 (즉 8.00 bytes/complex sample) 이라면, 그것은
> libairspy 가 FLOAT32_IQ (4 bytes/component) 를 흘려보냈고 HAL 이 그 바이트 스트림을 그대로
> 저장했다는 증거다 → **시나리오 1-bis 확정**.

> R2 실측 데이터 확보 시 다음 한 줄로 즉시 판정 가능:
> ```
> python diag/check_card_format.py /path/to/R2_capture.card
> ```
> 보고서의 "FORMAT VERDICT" 섹션이 INT16_IQ / FLOAT32 misread 중 하나로 직접 분류해 준다.

---

## 3. Phase C 결과: FFT 분석 (컨트롤 비교)

스크립트: `diag/check_fft_dualbin.py`, `diag/expected_carrier_bin.py`
로그: `diag/phase_c_fft_analysis.txt`
플롯: `diag/phase_c_fft_comparison_synth_int16_iq.png`,
       `diag/phase_c_fft_comparison_synth_float32_misread.png`

### 3.1 정상 INT16_IQ 컨트롤

INT16 해석으로 FFT 했을 때 **단일 강한 피크**가 관측됨:

```
bin    20  f=    3.05 kHz  mag=  96.32 dB
bin    19  f=    2.90 kHz  mag=  57.68 dB   (인접 누출)
bin    21  f=    3.20 kHz  mag=  57.44 dB   (인접 누출)
bin    18  f=    2.75 kHz  mag=  51.54 dB
bin    22  f=    3.36 kHz  mag=  51.39 dB
```

피크 1 개, peak-to-floor ≈ 40 dB. **RTL-SDR 기준 정상 동작과 일치.**

### 3.2 FLOAT32 misread 컨트롤

같은 합성 데이터를 float32-misread 경로로 저장한 뒤, **(잘못된) int16 해석**으로 FFT:

```
bin 65516  f= 4998.47 kHz  mag=  89.40 dB    ← 미러 피크
bin    20  f=    1.53 kHz  mag=  89.39 dB    ← 첫 번째 피크
bin 65476  f= 4995.42 kHz  mag=  79.82 dB    ← 미러 피크
bin    60  f=    4.58 kHz  mag=  79.80 dB    ← 두 번째 피크 ★
bin 65436  f= 4992.37 kHz  mag=  75.91 dB
bin   100  f=    7.63 kHz  mag=  75.17 dB    ← 세 번째 피크
```

**다중 피크 + 미러 + peak-to-floor 격차 축소.** 사용자 보고와 정성적으로 일치하는 패턴.

### 3.3 사용자 보고치와의 매칭

| 항목 | 사용자 보고 (R2 7_7_7) | INT16 컨트롤 | FLOAT32 misread 컨트롤 |
|---|---|---|---|
| 캐리어 피크 개수 | **2 개** (bin ~20, ~72) | 1 개 (bin 20) | **2~3 개** (bin 20, 60, 100, 미러 다수) |
| 캐리어 SNR | ~14 dB | ~40 dB | ~10–15 dB (피크-옆 peak 마진) |
| Δbin (관측) | 52 (≈ 7.93 kHz) | — | 40 (≈ 3.05 kHz) ※ |
| 정성적 일치 | — | ✗ (단일 피크) | ✔ (다중 피크) |

※ 사용자 보고의 Δbin=52 와 컨트롤의 Δbin=40 차이는 **TX 주파수·center freq·block_size·signal SNR 모두가
달라서** 절대 위치는 다르지만, "단일 캐리어가 다중 bin 으로 펼쳐지는 현상" 자체는 재현됨.
컨트롤은 합성 톤(3.05 kHz)에서 시작했지만 misread 시 1.53 kHz / 4.58 kHz / 7.63 kHz 의
강한 피크가 동시에 나타났다. 따라서 사용자 보고의 두 bin 도 **동일 메커니즘에 의한 "원래 한 개였던 캐리어의
잘못된 시간 정렬에 의한 분리"** 일 가능성이 매우 높다.

### 3.4 예상 캐리어 bin

`example/detector_r2.cfg` 의 `tuner_freq: 166M` 은 161.3 MHz TX 와 4.7 MHz 차이로 carrier_window
(bin 7-124, 즉 1.07–18.9 kHz) 를 한참 벗어남. 즉 프롬프트에 명시된 캡처는 **별도의 detector.cfg
(아마 `tuner_freq: 161.3M` 또는 매우 근접한 값)** 로 수행되었을 것이다. 이때:

- center=161.300 MHz → 기대 carrier bin = 0 (DC)
- 만약 center 가 ±수 kHz 어긋났다면 bin 20 (~3 kHz) 이 정확히 합리적 범위에 들어옴.

따라서 **"진짜" 캐리어는 bin 20** 하나이며, bin 72 는 포맷 손상으로 인한 인공물이라고 해석하는 것이
지금까지의 모든 증거와 부합한다.

---

## 4. 근본 원인 판정

### 1순위 가설 (강함): **`airspy_set_sample_type(INT16_IQ)` 실패의 silent fallback**

- 코드 흐름상 `set_sample_type` 의 ret 값이 0 이 아닌 경우 `logger.warning` 만 띄우고 무시한다
  (`airspy_mini.py:361-364`).
- libairspy 가 그 호출에 실패하면 디바이스는 **기본값인 FLOAT32_IQ** 로 동작한다.
- 콜백은 데이터를 `np.int16` 로 reinterpret 하므로, 모든 IQ 샘플의 바이트 정렬이 어긋나 spectrum
  이 깨진다.
- 합성 컨트롤 (Phase C 3.2) 에서 이 시나리오가 **다중 피크 + SNR 급락** 패턴을 재현했다.
- 검증 방법: `airspy_mini.py:361-364` 에 `raise DeviceConfigError(...)` 를 임시로 끼워 넣고
  R2 7_7_7 캡처를 다시 시도. 같은 환경에서 즉시 예외가 발생하면 1순위 가설이 확정된다.

### 2순위 가설 (보조): 다른 libairspy API 호출 (e.g. `set_packing`, `set_samplerate`) 이 일부 빌드에서 sample_type 을 리셋

- 일부 빌드에서 `airspy_set_samplerate` 직후 sample_type 이 기본값으로 돌아간다는 보고가 있다.
- 코드 흐름은 `device.open()` 에서 sample_type 설정 → `set_sample_rate()` 호출 (`airspy_capture.py:381`)
  순이므로 만약 R2 펌웨어/libairspy 빌드가 그런 동작을 하면 1순위와 동일한 결과가 된다.
- 검증 방법: `device.open()` 직후가 아니라 **`set_sample_rate` 직후**에 한 번 더 `set_sample_type`
  을 부르도록 임시 패치하고 R2 캡처 결과를 비교.

### 3순위 가설 (약함): R2 의 비정상 DC 누설

- bin 20 (~3 kHz) 이 진짜 캐리어, bin 72 가 R2 image leak 혹은 강한 노이즈 피크.
- 그러나 SNR 25 dB 강하는 단순 LO 누설 수준을 넘어 포맷 손상 쪽에 더 부합한다.

---

## 5. 권장 수정 방향 (이 보고서는 수정하지 않음)

- [ ] **HAL 의 `set_sample_type` 반환값 fail-fast 처리**: `airspy_mini.py:361-364` 의
      `logger.warning` 을 `raise DeviceConfigError(f"airspy_set_sample_type failed: {ret}")`
      으로 변경. fastcapture 의 `airspy_reader.c:201-202` 는 이미 `goto err` 로 fail-fast 임.
- [ ] **`set_sample_rate` 후 sample_type 재설정**: 안전 마진 차원에서 `_capture_airspy` 의
      `device.set_sample_rate(...)` 호출 직후 sample_type 재설정 메서드(예: `device.ensure_int16_iq()`)
      를 추가. 일부 libairspy 빌드의 리셋 이슈를 회피.
- [ ] **opening 시 sample_type 진단 출력**: `libairspy_version()` 과 함께 어떤 sample_type
      이 활성화되어 있는지 INFO 로그를 남기면 같은 증상이 또 발생했을 때 1초 만에 식별 가능.
- [ ] **선택적으로 `raw_to_complex` 의 정규화 상수**: 만약 R2 의 일부 빌드가 12-bit 데이터를
      full int16 로 확장하지 않고 **raw 12-bit 범위(-2048..+2047)** 로 흘려 보내는 경우가 있다면
      `/32768.0` 대신 `/2048.0` 로 적용해야 SNR 이 정상 회복된다.  Phase B 의 raw bytes 값과
      `as_int16.max()` 값이 함께 작으면(예: max ≤ 2047, std ≤ 1000) 이 경로를 의심.

---

## 6. 수정 후 검증 기준

- [ ] R2 7_7_7 캡처에서 단일 캐리어 bin 확인 (이중 bin 해소)
- [ ] `python diag/check_card_format.py <new_capture.card>` 가 "INT16_IQ scaled to FULL int16
      range" 판정 + bytes/complex sample == 4.00
- [ ] `python diag/check_fft_dualbin.py <new_capture.card>` 의 INT16 해석 FFT 가 단일 피크 +
      peak-to-floor ≥ 35 dB
- [ ] carrier SNR ≥ 35 dB, corr SNR ≥ 35 dB (RTL-SDR 기준치 비교)
- [ ] `apply_gain_mode('manual', lna=7, mixer=7, vga=7)` 로 설정했을 때 보고된 gain 값이
      0.00 dB 가 아닌 실제 값으로 표시

---

## 7. 빠른 재현 / 재실행 가이드

R2 캡처 파일(`.card`)을 확보하면 아래 한 줄로 본 보고서의 후속 분석을 자동 갱신할 수 있다.

```bash
cd /home/user/Thrifty-x
python3 diag/check_card_format.py /path/to/R2_capture.card  | tee diag/phase_b_real.txt
python3 diag/check_fft_dualbin.py  /path/to/R2_capture.card | tee -a diag/phase_c_real.txt
python3 diag/expected_carrier_bin.py                          | tee -a diag/phase_c_real.txt
```

- `phase_b_real.txt` 의 FORMAT VERDICT 라인이 1순위 가설을 즉시 검증한다.
- `phase_c_real.txt` 의 INT16 vs FLOAT32 해석 피크 비교가 보조 확인이다.

## 8. 산출물 목록 (`diag/` 디렉토리)

| 파일 | 설명 |
|---|---|
| `phase_a_code_analysis.md` | Phase A 정적 코드 분석 결과 (상세표 + 시나리오 체크리스트) |
| `check_card_format.py` | .card 파일의 v2 base64 페이로드를 디코드해서 int16 / float32 / uint8 로 비교 해석. `--all-blocks` 옵션으로 다중-블록 통계 가능 |
| `check_fft_dualbin.py` | .card 첫 블록의 FFT 를 INT16/FLOAT32 양쪽으로 그려 비교 |
| `check_signal_strength.py` | 모든 블록의 caller SNR / 노이즈 RMS / ADC 클리핑 / 캐리어 bin 히스토그램 + gain 권장값 |
| `expected_carrier_bin.py` | TX 주파수 / center / sample_rate 로 기대 carrier bin 계산 + cfg sniff |
| `_synth_card.py` | 정상 INT16 경로 + 가설 FLOAT32-misread 경로의 합성 .card 생성기 |
| `synth_int16_iq.card` | 정상 INT16_IQ 컨트롤 (262144 bytes/block) — gitignore |
| `synth_float32_misread.card` | 시나리오 1-bis 컨트롤 (524288 bytes/block) — gitignore |
| `phase_b_card_analysis.txt` | check_card_format 의 두 컨트롤 분석 출력 |
| `phase_b_synth.txt` | 합성 .card 생성 로그 |
| `phase_c_fft_analysis.txt` | check_fft_dualbin + expected_carrier_bin 출력 |
| `phase_c_fft_comparison_synth_int16_iq.png` | INT16 컨트롤의 FFT (단일 피크) |
| `phase_c_fft_comparison_synth_float32_misread.png` | FLOAT32-misread 컨트롤의 FFT (다중 피크) |
| `iq_format_diagnosis_report.md` | (본 보고서) |

---

## 9. Phase E — 실데이터 재검증 (UPDATE 2026-05-14)

### 9.1 테스트 대상

```
/home/batrf/github/Thrifty-x/example/
    gs_r2_161_3_20260513_152100_TX2_Gain000/b000/
        capture.card
        capture.log
        detect.log
        detector.cfg
```

### 9.2 capture.log 점검 결과

`airspy_set_sample_type() failed` 또는 sample_type 관련 경고가 **로그에 전혀 없음**.
유일한 경고는 bias_tee=true 관련 사용자 안내. → §1 의 **시나리오 1-bis (silent FLOAT32 fallback)
는 이 캡처에서 발생하지 않았음**.

### 9.3 capture.card 포맷 점검 결과

| 메트릭 | 측정값 | 해석 |
|---|---|---|
| 헤더 | `#v2 bit_depth=12 sample_rate=10000000` | 정상 v2 |
| base64-디코딩 후 블록 수 | 55 | — |
| 블록당 디코딩 바이트 (전 블록 동일) | **262144** | block_size 65536 × 2 (IQ) × 2 bytes (int16) = 262144 ✔ |
| bytes/complex sample | **4.00** | INT16 IQ |
| int16 해석 평균/표준편차 | min/max -153/+159, mean ≈ -0.29, std ≈ 15.94 | 정상 신호 (DC 근처 중심, 작은 진폭) |
| float32 해석 | finite_ratio ≈ 0.51, min/max -3.3e+38 / 1.5e-38, NaN/Inf 다수 | **명백히 비-float32** |

→ 본 캡처는 **정상적인 INT16_IQ v2 .card**. §1 의 1순위 가설은 이 데이터에서 **불성립**.

### 9.4 새 1순위 원인 (실데이터 기반)

`detector.cfg` 와 `capture.log` 확인 결과 **gain 단들이 전부 0** 으로 설정되어 있음:

```
lna_gain:   0
mixer_gain: 0
vga_gain:   0
gain mode: manual; LNA=0 Mixer=0 VGA=0   ← capture.log
```

이 때문에:

- carrier 피크 magnitude 가 threshold 바로 위 (`mag[16] ≈ 0.6–0.9`, threshold ≈ 0.6–0.7,
  noise ≈ 0.2),
- carrier SNR 대부분 12–14 dB 수준,
- correlation 은 거의 fail. `detect.log` 에서 `corr: yes` 인 블록은 손꼽을 정도
  (blk 1747 = 12.06 dB, blk 2141 = 12.26 dB, blk 2196 = 11.80 dB).

캐리어 bin 분포: 대부분 bin 16 (≈ 2.44 kHz @ 10 MSPS / 65536), 가끔 bin 32 / 48.
→ **단일 피크**, 즉 §3.2 의 다중-피크 (dual-bin) 현상은 이 캡처에서는 재현되지 않음.

> 따라서 이 캡처의 한정 원인은 **포맷 손상이 아니라 신호 강도 부족**.
> 시나리오 1-bis 는 *방어적 안전 마진* 차원에서는 여전히 의미가 있으나, 본 캡처의 직접 원인은 아니다.
> 원본 프롬프트가 언급한 7_7_7 캡처 (`...153826` 디렉토리, 캐리어 이중 bin + 14 dB) 와 본 캡처
> (`...152100_TX2_Gain000`, gain=0, 단일 bin + 12-14 dB) 는 **다른 실험 세션**으로 보이며,
> 원본 7_7_7 캡처가 확보되면 별도 재검증이 필요하다.

### 9.5 권장 조치 (실데이터 기반, 우선순위 순)

1. **gain 단계적 상향** — 동일 안테나/거리에서 다음 순서로 재캡처:
   - `lna=4 mixer=4 vga=4` → SNR 변화 확인
   - 부족하면 `lna=6 mixer=6 vga=6`
   - 그래도 부족하면 `lna=8 mixer=6 vga=6` (LNA 가 NF 에 가장 큰 영향)
   - 목표: carrier SNR ≥ 25 dB, ADC 클리핑 0
2. **bias_tee 점검** — 안테나 체인에 active LNA 가 없다면 `bias_tee: false`. 패시브 안테나에
   ~+4.5 V 가 흘러 들어가는 것을 막는다.
3. **fail-fast 패치 유지** — 본 캡처의 원인은 아니지만 `airspy_set_sample_type` 의 silent
   fallback 은 *언젠가는* 같은 종류의 버그를 가릴 가능성이 있으므로 안전 강화 차원에서
   `logger.warning` → `raise DeviceConfigError` 권장 (§5 항목 1번).
4. **template / timing 점검** — gain 을 올린 뒤에도 correlation 이 계속 실패하면 template
   mismatch / 타이밍 문제 의심. 본 보고서의 범위 밖.

### 9.6 새 도구 / 개선 사항

본 Phase E 의 follow-up 요청에 맞춰 `diag/` 에 추가/개선됨:

| 변경 | 파일 | 비고 |
|---|---|---|
| 강화 | `check_card_format.py` | `--all-blocks` 모드 추가: 카드 버전/헤더, bit_depth, sample_rate, 디코딩된 블록 수, unique 블록 사이즈, bytes/complex, int16 plausibility 전반 통계, float32 plausibility 전반 통계, 캐리어 bin 히스토그램 모두 한 번에 출력 |
| 신규 | `check_signal_strength.py` | 모든 블록의 carrier SNR / 노이즈 RMS / ADC 클리핑 / 캐리어 bin 히스토그램 + RMS amplitude 분포. 자동으로 sibling `detector*.cfg` 의 `carrier_window` / gain 읽어와서 한 줄짜리 RAISE/LOWER gain 권장 출력 |

### 9.7 빠른 재현 가이드

```bash
cd /home/user/Thrifty-x

# 포맷 확인 (전 블록 통계)
python3 diag/check_card_format.py <CARD> --all-blocks | tee diag/phase_e_format.txt

# Gain / SNR / 클리핑 진단 + 권장값
python3 diag/check_signal_strength.py <CARD> | tee diag/phase_e_signal.txt

# 기대 carrier bin
python3 diag/expected_carrier_bin.py | tee diag/phase_e_bin.txt
```

`<CARD>` 자리에 `capture.card` 전체 경로를 넣으면 §9.3 의 표가 자동 채워지고,
권장 gain 값이 직접 출력된다.

