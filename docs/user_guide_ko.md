# Thrifty-X 사용자 가이드

> Thrifty-X TDOA 측위 시스템의 종합 운용 매뉴얼.
> 영문판: [user_guide.md](user_guide.md).

## 목차

1. [소개](#1-소개)
2. [설치](#2-설치)
3. [지원 하드웨어](#3-지원-하드웨어)
4. [Gain 설정 이해하기](#4-gain-설정-이해하기)
5. [Configuration 레퍼런스](#5-configuration-레퍼런스)
6. [Template 시스템](#6-template-시스템)
7. [빠른 시작: 송신기 1대 / 수신기 1대 테스트](#7-빠른-시작-송신기-1대--수신기-1대-테스트)
8. [Command 레퍼런스](#8-command-레퍼런스)
9. [검출 결과 이해하기](#9-검출-결과-이해하기)
10. [트러블슈팅](#10-트러블슈팅)
11. [다중 수신기 TDOA 설정 (향후)](#11-다중-수신기-tdoa-설정-향후)
12. [라이선스 및 저작권](#12-라이선스-및-저작권)

---

## 1. 소개

**Thrifty-X**는 야생동물 추적 등 저비용 측위 응용을 위한 SDR(Software-Defined
Radio) 기반 도착시간차(TDOA) 측위 시스템입니다. 노스웨스트 대학교의
**Schalk Willem Krüger**가 MEng 학위논문에서 개발한 원본
[Thrifty](https://github.com/swkrueger/Thrifty)의 fork입니다. Thrifty-X는
원본의 신호 처리 파이프라인 — Dirichlet 커널 기반 carrier 보간,
sample-of-arrival(SoA) 추정, 비콘 기반 클럭 보정,
Levenberg-Marquardt 위치 해석 — 을 그대로 유지하면서 하드웨어 지원을
확장하고 코드베이스를 현대화했습니다.

**지원 하드웨어:** RTL-SDR (RTL2832U + R820T/R820T2), Airspy Mini, Airspy R2.

**원본 Thrifty와의 주요 차이점:**

| 항목 | 원본 Thrifty | Thrifty-X |
|---|---|---|
| SDR 지원 | RTL-SDR 전용 | RTL-SDR + Airspy Mini + Airspy R2 |
| Python | 2.7 / 초기 3 | 3.10+, 타입 힌트 |
| ADC | 8-bit unsigned | 8-bit (RTL) + 12-bit signed (Airspy) |
| Gain 제어 | 단일 tuner_gain | Airspy는 LNA + Mixer + VGA 단계별 |
| C 라이브러리 | fastcard (librtlsdr) | fastcapture (libairspy) |
| 시각화 | GNU Radio / osmosdr | matplotlib (FuncAnimation) |
| 패키징 | setup.py | pyproject.toml + setup.py |

**라이선스:** GPL-3.0-only (원본 Thrifty와 동일).

**인용:**

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system for
> tracking wildlife using off-the-shelf hardware.* 석사학위논문,
> North-West University, Potchefstroom Campus.
> https://hdl.handle.net/10394/25449

```bibtex
@mastersthesis{kruger2016inexpensive,
  title={An inexpensive hyperbolic positioning system for tracking wildlife
         using off-the-shelf hardware},
  author={Kr{\"u}ger, Schalk Willem},
  year={2016},
  school={North-West University (South Africa), Potchefstroom Campus}
}
```

---

## 2. 설치

### 2.1 요구사항

- Python **3.10 이상**
- NumPy >= 1.23, SciPy >= 1.9
- (선택) matplotlib >= 3.6 — `scope`, `analyze_*`, 플롯 기능에 필요
- (선택) libairspy — Airspy 라이브 캡처에 필요
- (선택) librtlsdr / `rtl_sdr` 바이너리 — RTL-SDR 라이브 캡처에 필요

### 2.2 Ubuntu 22.04 / WSL2 Ubuntu

```bash
# 시스템 패키지
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
                    build-essential cmake pkg-config \
                    airspy librtlsdr-dev rtl-sdr

# 클론 및 개발 모드 설치
git clone https://github.com/Denny-Hwang/Thrifty-x.git
cd Thrifty-x
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"          # numpy + scipy + matplotlib + 개발 도구
```

`[all]` extras는 `[fft]`(pyFFTW), `[analysis]`(matplotlib),
`[dev]`(pytest, mypy, ruff)를 포함합니다. 최소 런타임 + 플롯만 필요하면
`pip install -e ".[analysis]"`를 사용하세요.

### 2.3 udev 규칙 (Linux)

Airspy 장치는 일반 사용자 권한으로 접근 가능해야 합니다:

```bash
# airspyone_host(apt의 airspy 패키지) 규칙 사용
sudo cp /usr/share/airspy/52-airspy.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"    # 로그아웃 후 다시 로그인
```

RTL-SDR의 경우 `rtl-sdr`을 설치하고 커널 DVB 드라이버를 차단합니다:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
```

### 2.4 WSL2 USB Passthrough (Windows)

WSL2는 USB 장치를 기본적으로 인식하지 못하므로 Windows 호스트에
**usbipd-win**을 설치합니다:

```powershell
# Windows에서 관리자 권한 PowerShell
winget install usbipd
usbipd list
# VID:PID로 SDR 식별:
#   Airspy Mini / R2  = 1d50:60a1
#   RTL-SDR (RTL2832U + R820T2) = 0bda:2838
usbipd bind   --busid <X-Y>
usbipd attach --wsl --busid <X-Y>
```

`attach` 후 WSL 내부에서 장치가 보입니다. 확인:

```bash
lsusb                       # Bus … Device … 1d50:60a1 Airspy 등 표시
airspy_info                 # Airspy 시리얼 + 펌웨어 출력
rtl_test -t                 # RTL-SDR 테스트
```

`airspy_info`가 멈추거나 `airspy_open()`이 `-1000`을 반환하면 다른
프로세스(GNU Radio, SDR#, Gqrx)가 장치를 점유 중이거나 WSL의 USB
상태가 비정상입니다. PowerShell에서 `wsl --shutdown` 후 재연결하세요.

### 2.5 설치 확인

```bash
thriftyx --help                              # 명령어 목록 출력
python3 -c "import thriftyx; print(thriftyx.__version__)"
```

---

## 3. 지원 하드웨어

### 3.1 RTL-SDR (RTL2832U + R820T / R820T2)

- ADC: **8-bit** unsigned, I/Q 각 1 byte
- Sample rate: 0.9 – 2.4 MSPS (2.4 MSPS 권장)
- 주파수 범위: 24 – 1766 MHz
- Gain: 단일 dB 값 (R820T2의 LNA + Mixer는 드라이버가 자동 분배)
- 가격: ~$25 (클론), ~$35 (RTL-SDR Blog v3 / v4)
- 용도: 프로토타이핑, 원본 Thrifty와의 호환성 테스트

### 3.2 Airspy Mini

- ADC: **12-bit** signed
- Sample rate: 3 MSPS, 6 MSPS
- 주파수 범위: 24 – 1700 MHz
- Gain: 3단계 (LNA 0–14, Mixer 0–15, VGA/IF 0–15) — [Section 4](#4-gain-설정-이해하기) 참조
- Bias-tee: 지원 (4.5 V, ~50 mA — 외부 LNA / preamp 전원)
- 가격: ~$99
- 용도: 필드 배포 (소형, 저전력), 가성비 최적

### 3.3 Airspy R2

- ADC: **12-bit** signed
- Sample rate: 2.5 MSPS, 10 MSPS
- 주파수 범위: 24 – 1800 MHz
- Gain: 3단계 (동일한 R820T2 튜너)
- Bias-tee: 지원
- **외부 클럭 입력**: 지원 — 다중 수신기 coherent 동기화에 필수
- 가격: ~$169
- 용도: 최고 정밀도 TDOA (외부 클럭 + 10 MSPS)

### 3.4 하드웨어 비교

| 항목 | RTL-SDR | Airspy Mini | Airspy R2 |
|---|---|---|---|
| ADC 해상도 | 8-bit | 12-bit | 12-bit |
| 최대 sample rate | 2.4 MSPS | 6 MSPS | 10 MSPS |
| 원시 sample 주기 | 417 ns | 167 ns | 100 ns |
| 이론적 SoA 정밀도* | ~12 ns | ~5 ns | ~3 ns |
| 외부 클럭 입력 | 없음 | 없음 | **있음** |
| 위치 정확도** | ~3.5 m | ~1.5 m (목표) | ~1.0 m (목표) |

\* sub-sample 보간(parabolic / Gaussian, Krüger 2016) 적용 시.
\** 실환경 검증 진행 중. 3.5 m 수치는 Krüger 2016의 RTL-SDR 실험 결과.

---

## 4. Gain 설정 이해하기

> ⭐ **이 섹션은 사용자가 가장 많이 헷갈리는 영역입니다.** 수신기를
> 튜닝하기 전에 한 번은 반드시 읽으세요.

### 4.1 Gain이 중요한 이유

안테나에 도달하는 신호는 매우 미약합니다 — 예를 들어 멀리 떨어진
166 MHz 비콘은 **−80 dBm = 10 picowatts** 수준입니다. ADC가 의미 있게
디지털화하려면 SDR이 신호를 ADC의 동적 범위 안으로 증폭해야 합니다:

- **Gain이 너무 낮으면** → 신호가 ADC의 양자화 잡음에 묻힙니다
  (특히 RTL-SDR의 8-bit ADC에서 치명적). 검출 실패.
- **Gain이 너무 높으면** → ADC가 포화(clipping)되어 파형이 왜곡되고
  허위 검출이 발생합니다.
- **목표:** noise floor가 ADC 양자화 잡음을 **살짝 넘는** 수준에서,
  순간적인 burst를 위한 헤드룸을 남기는 것.

물 비유: 수도관(안테나) → 세 개의 밸브(gain stages) → 컵(ADC).
컵이 넘쳐서도 안 되고, 물이 너무 적어 맛볼 수 없어도 안 됩니다.

### 4.2 RTL-SDR Gain

RTL-SDR은 **단일** `tuner_gain` 값(dB)만 노출합니다. R820T2 드라이버가
내부적으로 LNA와 Mixer에 분배합니다. 일반적인 값:

- `0.0` — 자동 (드라이버 내부 AGC)
- `14.4` ~ `49.6` — 일반적인 수동 값 (드라이버가 가까운 단계로 스냅)

`detector.cfg`에 `tuner_gain: 0.0`으로 설정합니다. `fastcard` C
바이너리가 있으면 `-g <값>`으로 호출하고, 없으면 Python fallback이
librtlsdr을 통해 같은 값을 적용합니다.

> RTL-SDR scope/capture 로그에 표시되는 "gain = 0.00 dB"는 알려진
> 표시 문제로, 실제 동작에는 영향이 없습니다.

### 4.3 Airspy 3단계 Gain (LNA → Mixer → VGA)

Airspy의 R820T2 frontend는 **세 개의 독립적인 gain 단계**를 가집니다.
순서가 중요합니다:

#### Stage 1 — LNA (Low-Noise Amplifier), index 0–14

- **위치:** 안테나 직후 (RF 프론트엔드).
- **역할:** *첫 번째* 증폭기 — 가장 중요합니다. 여기서 증폭된 신호는
  뒤따르는 단계가 추가하는 잡음을 압도합니다. 이것이 Friis cascaded
  noise 원리입니다 — 단계 *N*의 잡음 지수는 앞 단계들의 gain으로
  나눠집니다.
- **비유:** 조용한 도서관에서 LNA는 마이크와 화자의 거리입니다. 가까울수록
  (LNA 높음) 목소리는 또렷하지만 숨소리도 함께 커집니다.
- **주의:** 대역 외 강한 신호(FM 방송, LTE)도 함께 증폭되어 후단에서
  상호변조 왜곡(IMD)을 만듭니다.

#### Stage 2 — Mixer, index 0–15

- **위치:** LNA 바로 뒤.
- **역할:** 주파수 변환 + 증폭. RF 신호와 local oscillator(LO)를
  혼합하여 원하는 대역을 다루기 쉬운 IF(R820T2 내부 ~5 MHz)로 옮깁니다.
- **비유:** 빠르게 회전하는 바퀴(RF)에 손전등(LO)을 비추면 천천히
  움직이는 듯한 영상(IF)이 보이고, 손전등 밝기가 시각적 gain을
  제어합니다.
- **주의:** 강한 신호 환경에서는 VGA에 손대기 전에 LNA와 함께 Mixer를
  먼저 줄이세요.

#### Stage 3 — VGA / IF (Variable Gain Amplifier), index 0–15

- **위치:** Mixer 뒤, ADC 직전.
- **역할:** 최종 ADC 입력 레벨 미세 조정.
- **비유:** 오디오 시스템의 마스터 볼륨 — 키우면 음악도, "쉬쉬"
  잡음도 함께 커집니다.
- **주의:** VGA를 올린다고 SNR이 개선되지 **않습니다**. LNA와 Mixer가
  이미 noise floor를 결정해 놓았다면 VGA는 그것을 비례 확대만 합니다.
  VGA는 적당한 수준으로 유지하세요.

#### Index와 dB의 관계

Index는 R820T2 칩 내부 레지스터 값으로 **dB가 아닙니다**. Step별
gain은 비선형입니다:

| Stage | Index 범위 | 대략적 dB 범위 | Step |
|---|---|---|---|
| LNA   | 0–14 | 0 ~ ~26 dB | 비균일 |
| Mixer | 0–15 | 0 ~ ~19 dB | 비균일 |
| VGA   | 0–15 | 0 ~ ~26 dB | ~1.5 dB / step (가장 선형) |

3단계 합산 최댓값은 ~65 dB.

### 4.4 Gain 모드 (manual / linearity / sensitivity)

libairspy는 세 가지 프리셋 모드를 제공합니다. Thrifty-X에서는
`--gain-mode`로 노출됩니다:

| 모드 | 동작 | 사용 시점 |
|---|---|---|
| **manual** *(기본)* | LNA, Mixer, VGA index를 직접 적용. | 완전한 제어, 디버깅. |
| **linearity** | LUT 기반: LNA를 먼저 줄이고 VGA는 유지 — IMD 최소화. | 강한 신호 환경 (도시, 송신기 근처). |
| **sensitivity** | LUT 기반: LNA를 높게 유지하고 VGA부터 줄임 — noise figure 최소화. | 약한 신호 환경 (시골, 원거리 TX). |

`linearity`와 `sensitivity` 모드에서는 단일 **combined-gain** 값
(0–21, 0=최대, 21=최소)으로 단순화됩니다. libairspy의 실제 LUT 일부:

```
Linearity 모드 (combined index → LNA, Mixer, VGA):
   0:  14, 12, 14    (최대 gain)
   5:  10, 10,  9
  10:   8,  7,  5
  15:   0,  3,  1
  21:   0,  0,  0    (최소 gain)

Sensitivity 모드:
   0:  14, 12, 13    (최대 gain)
   5:  14, 10,  8
  10:  12,  7,  5
  15:   7,  2,  4
  21:   0,  0,  4    (최소 gain)
```

### 4.5 Gain 튜닝 절차

두 Airspy 장치 모두에서 재현 가능한 절차:

1. **안테나 분리.** `thriftyx scope` 실행 후 noise floor 관찰
   (FFT 패널).
2. **안테나 연결** (50 Ω 더미 로드를 중간 단계로 사용하면 좋음).
   noise floor가 **2–3 dB**만 올라가야 정상. 이보다 크면 LNA가 너무
   높거나 강한 대역 외 신호가 있습니다.
3. 적당한 베이스라인에서 시작: `LNA=7, Mixer=7, VGA=7`
   (`detector_mini.cfg` / `detector_r2.cfg` 기본값).
4. **LNA부터 올리며** noise floor를 관찰. noise floor가 눈에 띄게
   올라가기 *직전* 단계에서 멈춥니다.
5. **Mixer로 미세 조정** — 대역 내 신호 레벨을 맞춥니다.
6. **VGA는 마지막**, ADC 입력 레벨 맞추기 전용. `analyze_detect ...
   -p overview`의 sample histogram이 ±2047에서 잘리면 VGA를
   낮추세요.

**권장 시작 값:**

| 장치 | LNA | Mixer | VGA | 환경 |
|---|---|---|---|---|
| Airspy Mini (일반) | 10 | 10 | 10 | 중거리, 보통 RF 환경 |
| Airspy Mini (약신호) | 14 | 12 |  8 | 원거리, 깨끗한 RF 환경 |
| Airspy R2 (일반) | 10 | 10 | 10 | 중거리 |
| Airspy R2 (약신호) | 14 | 14 | 12 | 원거리, 깨끗한 RF 환경 |
| Airspy R2 (강신호) |  5 |  5 |  8 | 근거리, 도시 RF |

> ⚠️ `LNA=14, Mixer=15, VGA=15`는 절대 최댓값으로, 사소한 입력에도
> ADC를 포화시킵니다. 이 설정에서 noise 필드가 90을 넘는 것을
> 관측한 사례가 있습니다 — 각 단계에서 최소 한 칸씩 물러나세요.

### 4.6 Gain 문제 진단

| 증상 | 가능한 원인 | 조치 |
|---|---|---|
| 검출 0개 | Gain 너무 낮음 | LNA부터 올리기 |
| capture 상태 라인의 noise 값 >> 10 | Gain 너무 높음 | VGA부터 낮추기 |
| 이상한 bin에서 산발적 corr 히트 | IMD (LNA 과다) | LNA 낮추기 |
| Histogram이 −2048 / +2047에 집중 | ADC clipping | 전체 chain 낮추기 |
| `gain = 0.00 dB` 표시 (RTL-SDR) | 단순 표시 문제 | 무시 |

---

## 5. Configuration 레퍼런스

### 5.1 `detector.cfg` 형식

`key: value` 한 줄 단위, `#`은 주석. 모든 Thrifty-X 명령이 동일한
파서를 사용하므로 단일 `detector.cfg`가 `capture`, `detect`, `scope`,
`template_*` 등을 모두 커버합니다.

수치 접미사: `K`, `M`, `G` (예: `2.4M = 2_400_000`).
`carrier_window`와 threshold 표현식은 `thriftyx.setting_parsers`가
파싱합니다.

CLI 플래그는 항상 config 값을 덮어씁니다.

### 5.2 장치별 Config 예시

`example/` 디렉토리에는 사전 튜닝된 세 가지 config가 있습니다.

**`example/detector.cfg` — RTL-SDR @ 2.4 MSPS (기본값):**

```
rxid:               0
device_type:        rtlsdr
bit_depth:          8
sample_rate:        2.4M
chip_rate:          0.999707M
tuner_freq:         433.83M           # 송신기에 맞게 조정
tuner_gain:         0.0
capture_skip:       600
block_size:         16384             # 2^14, 2.4 MSPS에서 ~6.83 ms
block_history:      4920              # >= template length (2455)
carrier_window:     7 - 130           # ~1 kHz ~ ~19 kHz offset
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer           # 또는 'time_domain'
soa_interpolation:  parabolic         # 또는 'gaussian' / 'none'
```

**`example/detector_mini.cfg` — Airspy Mini @ 6 MSPS:**

```
rxid:               0
device_type:        airspy_mini
bit_depth:          12
sample_rate:        6M
chip_rate:          0.999707M
tuner_freq:         166M
capture_skip:       100
lna_gain:           7                 # 0–14
mixer_gain:         7                 # 0–15
vga_gain:           7                 # 0–15
bias_tee:           false
block_size:         32768             # 2^15, 6 MSPS에서 ~5.46 ms
block_history:      12278             # 2 × template length (6139)
carrier_window:     6 - 103           # 1 kHz ~ 19 kHz @ 183.1 Hz/bin
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer
soa_interpolation:  parabolic
```

**`example/detector_r2.cfg` — Airspy R2 @ 10 MSPS:**

```
rxid:               0
device_type:        airspy_r2
bit_depth:          12
sample_rate:        10M
chip_rate:          0.999707M
tuner_freq:         166M
capture_skip:       100
lna_gain:           7
mixer_gain:         7
vga_gain:           7
bias_tee:           false
block_size:         65536             # 2^16, 10 MSPS에서 ~6.55 ms
block_history:      20464             # 2 × template length (10232)
carrier_window:     7 - 124           # 1 kHz ~ 19 kHz @ 152.6 Hz/bin
carrier_threshold:  15 * snr
corr_threshold:     15 * snr
template:           template.npy
freq_shift_method:  integer
soa_interpolation:  parabolic
```

장치를 바꾸려면 해당 파일을 `detector.cfg` 위에 복사하세요:

```bash
cp example/detector_mini.cfg example/detector.cfg
```

### 5.3 파라미터 의존성

`sample_rate`를 바꾸면 여러 파라미터가 연쇄적으로 영향을 받습니다.
아래 표를 항상 일관되게 유지하세요.

| 파라미터 | 공식 | RTL @ 2.4 M | Mini @ 6 M | R2 @ 10 M |
|---|---|---|---|---|
| template length | (2^code_len − 1) × sample_rate / chip_rate | 2,457 | 6,139 | 10,232 |
| `block_size`     | ≥ 2 × `block_history`, 2의 거듭제곱 | 16,384 | 32,768 | 65,536 |
| `block_history`  | ≥ template length | 4,920 | 12,278 | 20,464 |
| block 주기       | `block_size` / `sample_rate` | 6.83 ms | 5.46 ms | 6.55 ms |
| bin 해상도       | `sample_rate` / `block_size` | 146.5 Hz | 183.1 Hz | 152.6 Hz |
| `carrier_window` low  | `ceil(1000 / bin_res)` | 7 | 6 | 7 |
| `carrier_window` high | `floor(19000 / bin_res)` | 130 | 103 | 124 |

> ⚠️ `template.npy`와 `detector.cfg`는 **반드시 같은 `sample_rate`를
> 공유**해야 합니다. 불일치 시 검출이 0건이 됩니다. sample rate를
> 바꿀 때마다 template을 재생성하세요
> ([Section 6.5](#65-장치-변경-시-template-재생성)).

### 5.4 자주 사용하는 Airspy CLI 플래그

| 플래그 | 기본값 | 비고 |
|---|---|---|
| `--lna-gain N` / `--mixer-gain N` / `--vga-gain N` | config | `manual` 모드에서 단계별 index. |
| `--gain-mode {manual, linearity, sensitivity}` | `manual` | gain 테이블 선택. |
| `--combined-gain N` | 0 | 0–21, linearity/sensitivity 모드에서 사용. |
| `--lna-agc` / `--mixer-agc` | false | R820T2 AGC 루프 활성화. |
| `--bias-tee` | false | 안테나 라인에 4.5 V — DC 격리 확인 필수. |
| `--ppm F` | 0 | LO 보정 (ppm). |
| `--packing` | false | libairspy 12-bit USB packing (10 MSPS R2에 유용). |
| `--airspy-serial 0x…` | – | 64-bit 시리얼로 Airspy 선택. |
| `-d N` / `--device-index N` | 0 | 열거 순서로 장치 선택. |

---

## 6. Template 시스템

> ⭐ **두 번째로 자주 헷갈리는 영역.** 검출 성능은 *이론적* template이
> 아닌 *실측* template에 좌우됩니다.

### 6.1 Template이란?

Thrifty-X 송신기는 Gold-code로 변조된 연속파 신호를 송출합니다.
**Gold code**는 길이 `(2^n − 1)` 이진 시퀀스로, 자기상관 특성이
좋아 GPS C/A 코드와 CDMA에 동일하게 사용됩니다. Thrifty-X는
`code_len = 10`을 사용하므로 한 코드 주기당 1023 chip입니다.

각 송신기에는 고유한 `code_index`(0 ~ 1024)가 할당됩니다. **Template**은
이 Gold code를 수신기의 sample rate로 리샘플링한 결과입니다. 검출은
캡처된 블록과 이 template 사이의 FFT 기반 correlation으로 수행됩니다.

### 6.2 이론적 Template

```bash
thriftyx template_generate <code_len> <code_index> -o template.npy
# 예: code length 10, transmitter index 3
thriftyx template_generate 10 3 -o template.npy
```

출력은 설정된 sample rate의 깔끔한 `{−1, +1}` BPSK 사각파입니다.
**하드웨어 없이도** 생성 가능하지만, 수신기의 아날로그 frontend
응답과 일치하지 않으므로 correlation SNR이 낮습니다 (mismatched
filter).

### 6.3 실측 Template (강력 권장)

실제 캡처에서 matched filter를 직접 추출합니다:

```bash
# Step 1 — 이론적 seed template 생성
thriftyx template_generate 10 3 -o template_ideal.npy

# Step 2 — 짧은 라이브 캡처 (5–10초로 충분)
thriftyx capture initial.card --duration 10

# Step 3 — 캡처에서 연속값 template 추출
thriftyx template_extract initial.card \
    --template template_ideal.npy \
    -o template_captured.npy

# Step 4 — 활성 template으로 사용
cp template_captured.npy template.npy
```

추출된 template은 `±1`이 아닌 연속값을 가지며, 아날로그 frontend의
펄스 정형, 필터 리플, 군지연 등 — 즉 *이* 수신 체인의 진짜 matched
filter — 정보를 담고 있습니다.

실제 캡처에서의 correlation SNR 개선:

| Template | RTL-SDR | Airspy Mini | Airspy R2 |
|---|---|---|---|
| 이론적 (±1) | ~13 dB | ~5 dB | ~7 dB |
| **실측** | **~56 dB** | **~41 dB** | **~39 dB** |

30 dB 이상 차이로, 이론적 template만으로는 원거리 검출이 아예 실패할
수 있습니다. **본격적인 운용 전에 반드시 실측 template을
추출하세요.**

### 6.4 Gold-code Index를 모를 때

송신기의 `code_index`를 모르면 brute-force 탐색을 합니다. 0~20이면
대부분의 필드 배포를 커버합니다:

```bash
for i in $(seq 0 20); do
  thriftyx template_generate 10 $i -o /tmp/template_test.npy
  count=$(thriftyx detect capture.card \
      --template /tmp/template_test.npy -o /dev/null 2>&1 \
      | grep -c "corr: yes")
  echo "index $i: $count corr hits"
done
```

가장 많은 `corr: yes`를 내는 index가 송신기 설정입니다. 식별 후
정식 운용 전에 적절한 template을 다시 생성/추출하세요.

### 6.5 장치 변경 시 Template 재생성

`template.npy`는 **sample rate에 종속**입니다. RTL-SDR에서 Airspy
Mini로 바꾸면 코드 주기당 sample 수가 2,457 → 6,139로 변하고, 기존
template은 더 이상 correlate하지 않습니다. sample rate(또는 장치)를
바꿀 때마다:

1. `cp example/detector_<device>.cfg example/detector.cfg`
2. `thriftyx template_generate 10 <code_index> -o template_ideal.npy`
3. `thriftyx capture initial.card --duration 10`
4. `thriftyx template_extract initial.card --template template_ideal.npy -o template.npy`

---

## 7. 빠른 시작: 송신기 1대 / 수신기 1대 테스트

전체 first-light 파이프라인. 비콘 송신기 1대가 송출 중인 상태에서
`example/`에서 실행하세요.

```bash
# 0. 환경 활성화
cd ~/Thrifty-x
source .venv/bin/activate
cd example

# 1. 장치별 config 선택
cp detector_r2.cfg detector.cfg          # 사용 하드웨어에 맞게

# 2. 이론적 seed template 생성
thriftyx template_generate 10 3 -o template_ideal.npy

# 3. Template 추출용 짧은 캡처
thriftyx capture initial.card --duration 5

# 4. 실측 (matched) template 추출
thriftyx template_extract initial.card \
    --template template_ideal.npy -o template.npy

# 5. 본격 캡처 (30초)
thriftyx capture rx0.card --duration 30

# 6. 검출 (carrier + correlation) → .toad
thriftyx detect rx0.card -o rx0.toad

# 7. 송신기 ID 식별 → .toads
thriftyx identify rx0.toad -o rx0.toads

# 8. 통계 및 분석
thriftyx analyze_toads -i rx0.toads
thriftyx analyze_detect rx0.card -m 2 -p overview
```

**각 단계에서 성공의 모습:**

- Step 3 / 5 — 검출된 블록마다 stderr에
  `block #N: mag[bin] = … (thresh = …, noise = …)` 라인.
- Step 6 — stdout에 `block #… cardet: yes corr: yes …` 라인;
  `corr: yes` 라인 수가 검출 수입니다.
- Step 7 — 고유한 송신마다 `rx0.toads`에 한 줄.
- Step 8 — `analyze_toads`는 요약 통계 출력;
  `analyze_detect`는 4-패널 overview 플롯 표시.

`corr: yes`가 드물거나 없으면 Section 4 (gain)와 Section 6
(template)으로 돌아가세요.

---

## 8. Command 레퍼런스

모든 명령은 `thriftyx`의 서브커맨드입니다 (legacy alias `thrifty`도
사용 가능). 옵션 전체 목록은 `thriftyx help <command>`로 확인하세요.
디스패치 테이블은 `thriftyx/cli.py`에 있습니다.

### Core 파이프라인

| Command | 한 줄 설명 | 입력 → 출력 |
|---|---|---|
| `capture` | Carrier-detection prefilter로 SDR 캡처 | SDR → `.card` |
| `detect`  | Carrier sync + correlation, SoA 추정 | `.card` → `.toad` |
| `identify` | 검출을 송신기 ID로 매핑, 중복 제거 | `*.toad` → `.toads` |
| `match` | 수신기 간 시간창 매칭 | `.toads` → `.match` |
| `tdoa` | Beacon 보정 TDOA 추정 | `.toads` + `.match` → `.tdoa` |
| `pos` | Levenberg-Marquardt 위치 해석 | `.tdoa` → `.pos` |

### 분석

| Command | 한 줄 설명 |
|---|---|
| `scope` | matplotlib 기반 라이브 시간/FFT/histogram 플롯. `--trigger-level <0–1>`로 peak hold. |
| `analyze_toads` | `.toads` 파일에 대한 요약 통계. `-i data.toads -m data.match`. |
| `analyze_detect` | 진단 플롯과 함께 검출 재실행. `-m N` (최대 블록), `-p overview,time,overlays,spectra,corrs`. |
| `analyze_beacon` | 두 수신기 간 비콘의 SoA 차이. `--beacon`, `--rx0`, `--rx1`. |
| `analyze_tdoa` | `.tdoa` 데이터 슬라이스별 통계. `--rx0`, `--rx1`, `--tx`, `--timestamp`. |

### 유틸리티

| Command | 한 줄 설명 |
|---|---|
| `template_generate` | 이상적 Gold-code template 생성. `length` `index` `-o file.npy`. |
| `template_extract`  | 캡처에서 matched template 추출. `input.card --template ideal.npy -o new.npy`. |

### 공통 옵션

- `-o / --output` — stdout 대신 파일에 기록.
- `-a / --append` — 기존 출력 파일에 추가 (`detect`만).
- `--quiet` — 블록별 상태 출력 억제 (`detect`).
- `--raw` — `.card`가 아닌 raw I/Q 입력 (`detect`,
  `analyze_detect`).

### 주요 `capture` 옵션

- `--device-type {rtlsdr, airspy_mini, airspy_r2}` — config 덮어쓰기.
- `--duration <sec>` — N초 후 정지 (기본: Ctrl+C까지).
- `--input <path>` — 라이브 장치 대신 파일이나 `-`(stdin)에서 읽기.
  `rtl_sdr -f … -s … - | thriftyx capture …` 형태로 사용.
- `--fastcard <path>` — `fastcard` 바이너리 경로 (RTL-SDR 전용).
  `PATH`에 없으면 Thrifty-X가 Python carrier detector로 fallback.

---

## 9. 검출 결과 이해하기

### 9.1 `.card` 파일 형식

`.card` 파일에는 carrier가 검출된 블록만 저장됩니다 (원본 Thrifty의
`fastcard` 동작과 동일). 디스크 형식은 두 가지:

- **v1** (RTL-SDR legacy, 헤더 없음) — 라인:
  `<timestamp> <block_idx> <raw uint8 I/Q의 base64>`.
- **v2** (Airspy) — 헤더 라인
  `#v2 bit_depth=12 sample_rate=6000000`,
  이후 `<timestamp> <block_idx> <int16 I/Q의 base64>` 라인.

`thriftyx.block_data.card_reader`가 형식을 자동 감지하므로, 원본
Thrifty로 캡처한 v1 RTL-SDR 데이터도 변환 없이 사용 가능합니다.

### 9.2 `.toad` 파일 형식

검출 한 건에 한 줄, 공백 구분:

| 컬럼 | 의미 |
|---|---|
| `rxid` | 수신기 ID (`detector.cfg`의 `rxid:`) |
| `timestamp` | 블록의 Linux epoch 시각 |
| `block_idx` | 캡처 내 블록 번호 |
| `soa` | Sample-of-arrival (sub-sample 정밀도) |
| `corr_idx`, `corr_offset`, `corr_energy` | Correlation 피크 메타데이터 |
| `carrier_idx`, `carrier_offset`, `carrier_energy` | Carrier 검출 메타데이터 |
| `noise_rms`, `block_energy` | Threshold 산정용 noise/energy 통계 |

`identify`가 만드는 `.toads`에는 `txid` 컬럼이 추가되고 수신기별
중복이 제거됩니다.

### 9.3 검출 분석 플롯

`thriftyx analyze_detect <file.card> -m <N> -p <plots>`는 처음 `N`개
블록에 대해 검출기를 재실행하여 다음 플롯 군을 렌더링합니다.
`-p overview`부터 시작하세요.

1. **overview** — 4 패널: sample histogram + 시간에 따른 frequency
   보정 magnitude + FFT (carrier 탐색) + correlation 출력.
2. **time** — 시간 도메인 파형 (real + imaginary, magnitude).
3. **overlays** — 정렬된 상태에서 captured 신호와 template 겹침.
4. **spectra** — magnitude 스펙트럼, carrier window 음영.
5. **corrs** — cross-correlation vs. autocorrelation, sub-sample
   보간(parabolic / Gaussian) overlay.

"좋은" 모습:

- **Histogram** 0 부근 중심, ±127 (RTL-SDR) / ±2047 (Airspy)에
  몰림 없음.
- **FFT** carrier window 안에 뚜렷한 carrier 피크.
- **Correlation** 하나의 큰 피크와 낮은 sidelobe floor.
- **Overlays**에서 template과 signal이 서로 추적.

"나쁜" 모습:

- Histogram 양 끝에 몰림 → ADC clipping (gain 낮추기).
- Correlation 피크가 noise에 묻힘 → template 오류, gain 부적절,
  또는 잘못된 `code_index`.
- Carrier 피크가 window 밖 → `tuner_freq` 또는 `carrier_window`
  조정.

---

## 10. 트러블슈팅

### 10.1 자주 발생하는 문제

| 증상 | 가능한 원인 | 조치 |
|---|---|---|
| `usb_claim_interface error -6` | Ctrl+C 후 stale USB handle | `usbipd detach` → `usbipd attach`; 또는 `udevadm trigger` |
| `airspy_info` 멈춤 | WSL USB 상태 비정상 | PowerShell에서 `wsl --shutdown` 후 재연결 |
| `airspy_open() returned -1000` | 다른 프로세스가 장치 점유 | GNU Radio / SDR# / Gqrx 종료 |
| 검출 0개 | Wrong Gold-code index | Brute-force 탐색 (Section 6.4) |
| 검출 0개 | Gain 너무 낮음 | LNA 올리기 (Section 4.5) |
| 검출 0개 | Template ↔ config sample rate 불일치 | Template 재생성 (Section 6.5) |
| `corr: no` 만 발생 | 이론적 template만 사용 중 | 실측 template 추출 (Section 6.3) |
| Carrier가 예상 외 bin | 주파수 offset / 잘못된 `tuner_freq` | `thriftyx scope`로 실제 carrier 위치 확인 |
| Noise 값 매우 큼 | Gain 과다 (특히 VGA) | VGA → Mixer 순으로 낮추기 |
| Histogram이 ±max에 몰림 | ADC 포화 | 전체 chain 낮추기 |
| `airspy_start_rx() failed: -1000` | 불안정한 USB 링크 | `usbipd` 재연결; USB 3.x 포트 사용 |
| 블록 간격 불규칙 | USB 버퍼 드롭 | `capture_skip` 늘리기; 포트 변경; R2는 `--packing` 활성화 |

### 10.2 WSL2 관련 팁

- USB attach / detach는 **PowerShell**에서. WSL 안에서 하면 안 됨.
- WSL 내부에서 matplotlib 플롯을 보려면 백엔드 설정:
  `export MPLBACKEND=TkAgg` (WSLg 사용 시), 또는 `--export plot.pdf`로
  파일 저장.
- USB 상태가 꼬이면 `wsl --shutdown`이 깔끔한 회복 수단.
- WSL의 시계가 드리프트할 수 있음 — `.toad` timestamp가 이상하면
  `sudo hwclock -s` 실행.

---

## 11. 다중 수신기 TDOA 설정 (향후)

다중 수신기 측위는 Thrifty-X에서 **개발 진행 중**입니다. 상위 구조
(원본 Thrifty 계승):

- **수신기 최소 3대**, **알려진 위치의 비콘 송신기 1대**, **태그
  송신기 N대**.
- 비콘의 알려진 위치를 사용하여 수신기들의 비동기 클럭을 보정합니다 —
  수신기 간의 모든 TDOA는 비콘 송출에 anchor됩니다.
- **Coherent 동기화**(공통 10 MHz 레퍼런스 공유)는 **Airspy R2의
  외부 클럭 입력**에서만 가능합니다. Mini / RTL-SDR은 비콘 기반
  보정에 한정됩니다.

파이프라인 단계 (CLI 명령):

```
*.toads  →  thriftyx match    → .match
.toads + .match  →  thriftyx tdoa  -r pos-rx.cfg -b pos-beacon.cfg → .tdoa
.tdoa  →  thriftyx pos  -r pos-rx.cfg → .pos
```

수신기와 비콘 좌표는 `pos-rx.cfg`, `pos-beacon.cfg`에 저장됩니다 (한
줄에 `id: x y` 형식). End-to-end 다중 수신기 문서는 통합 테스트가
성숙해지는 대로 추가됩니다.

---

## 12. 라이선스 및 저작권

Thrifty-X는 **GPL-3.0-only**로 라이선스됩니다 (upstream 프로젝트와
동일). 전체 조항은 [LICENSE.txt](../LICENSE.txt) 참조.

- 원본 Thrifty © 2016–2017 Schalk Willem Krüger, North-West
  University. 소스:
  [github.com/swkrueger/Thrifty](https://github.com/swkrueger/Thrifty).
- Thrifty-X © 2025–2026 Sungjoo Hwang 및 PNNL contributors.

Thrifty-X로 얻은 결과를 발표할 때는 원본 학위논문을 인용해 주세요:

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system
> for tracking wildlife using off-the-shelf hardware.* 석사학위논문,
> North-West University, Potchefstroom Campus.
> https://hdl.handle.net/10394/25449

```bibtex
@mastersthesis{kruger2016inexpensive,
  title={An inexpensive hyperbolic positioning system for tracking wildlife
         using off-the-shelf hardware},
  author={Kr{\"u}ger, Schalk Willem},
  year={2016},
  school={North-West University (South Africa), Potchefstroom Campus}
}
```
