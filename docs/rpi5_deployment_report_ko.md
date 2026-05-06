# Raspberry Pi 5 배포 분석 및 실행 계획 (독립형 RX Site)

작성일: 2026-05-06
대상 저장소: `Thrifty-x` (브랜치 `claude/raspberry-pi-deployment-c3WTH`)
선행 PR: #25 (Codex, Pi5 배포 리포트 초안)

> 본 문서는 PR #25(Codex)의 초안을 **실제 코드 분석 결과**로 보강하고,
> 초안에서 P0로 식별했지만 실제로는 추가되지 않았던 산출물
> (Pi5 전용 설치 문서, systemd 템플릿)을 함께 본 PR에 포함한다.

---

## TL;DR

- **배포 가능성:** 높음. 코드/의존성 측면에서 Pi 5(64-bit) 구동 가능.
- **즉시 무인 운영 가능성:** **아직 아님.** 아래 P0 항목 적용 + 실기 검증 필요.
- **현재 PR로 해결된 것:** Pi5 전용 설치 가이드, systemd 템플릿, 운영 런북, 코드 레벨 위험 항목 정리.
- **다음 단계:** 실제 Pi 5 + Airspy Mini/R2 조합으로 `docs/rpi5_validation_checklist_ko.md` 1회 완주.

---

## 1) 코드/구성 레벨 분석 (Codex 초안에 추가)

### 1.1 의존성 — Pi 5 (Bookworm aarch64) 호환성

| 항목 | 상태 | 비고 |
|---|---|---|
| `numpy>=1.23`, `scipy>=1.9` | OK | piwheels/manylinux2014_aarch64 wheel 제공 |
| `matplotlib>=3.6` (선택) | OK | 무인 노드는 `MPLBACKEND=Agg` 권장 |
| `pyfftw>=0.13` (선택) | 조건부 OK | apt `python3-pyfftw` 또는 pip; 빌드 시 `libfftw3-dev` 필요. **레거시 fftwl 패치는 불필요**(64-bit 환경) |
| `libairspy` | **버전 주의** | HAL이 `airspy_open_sn`, `airspy_list_devices`, `airspy_get_samplerates`, `airspy_set_packing`을 사용. Bookworm `airspy` 패키지(1.0.10)는 모두 제공. 그 이전 빌드면 `serial`/`packing` 옵션이 silently 무시될 수 있음. (`thriftyx/hal/airspy_mini.py:340-352`, `:618-633`) |
| `fastcard`/`fastdet` C 바이너리 | **불필요** | 본 포크는 RTL-SDR 경로에서만 fastcard 호출. Airspy 운용 노드에서는 빌드/설치 불필요. 기존 `rpi/installation.md`의 fastcard/libvolk/fftwl 절차는 Pi 5에 부적합. |

### 1.2 캡처 핫루프 성능 이슈 (신규 식별)

`thriftyx/airspy_capture.py:275`, `:454` 의 캡처 루프는 `np.fft.fft`를
직접 호출한다. 같은 저장소의 `thriftyx/signal_utils.py`에는 pyfftw가
설치되어 있을 때 자동으로 가속해주는 `compute_fft()`가 이미 있는데,
캡처 경로에서는 사용되고 있지 않다.

- 영향: Pi 5 Cortex-A76에서 6 MSPS(Airspy Mini) 또는 10 MSPS(R2) +
  큰 `block_size`(예: 16384/32768) 조합 시 single-thread FFT가
  병목이 될 수 있음. pyfftw는 동일 size 반복 사용 시 plan 캐싱으로
  2~5× 가속됨.
- 권장 개선: 캡처 루프에서 `compute_fft`를 사용하거나, 모듈
  레벨에서 `pyfftw.builders.fft(block_size)` plan을 1회 생성해
  재사용. (P1, 코드 변경 필요 — 본 PR 범위 외)

### 1.3 SD 카드 wear 이슈 (신규 식별)

`airspy_capture.py:285,466`에서 탐지 블록마다 `output_file.flush()`를
호출한다. 검출률이 높을 때 microSD에 작은 동기 write가 반복되어
수명/지연에 불리하다.

- 권장: (a) 출력 파일을 USB SSD/HDD에 두거나, (b) tmpfs 링버퍼 +
  주기적 flush(예: 1초/100블록), (c) 노드 운영 시 외장 스토리지를
  기본값으로 가이드. systemd 템플릿(`Environment=THRIFTYX_OUT=...`)
  에서 외장 마운트로 분리.

### 1.4 신호 핸들링과 systemd 상호작용

- `_capture_airspy`는 `DeviceNotFoundError`/`DeviceConfigError` 시
  `sys.exit(1)`로 종료한다(`airspy_capture.py:351,475,478`). 이는
  systemd `Restart=on-failure` + `RestartSec`와 잘 맞물림 — **부팅
  시 USB enumeration 지연으로 첫 시도가 실패해도 자동 재시도** 된다.
- SIGINT/SIGTERM 핸들러로 `running[0] = False`만 세팅 후 `finally`
  블록에서 `device.close()` 호출 — 정상 종료 경로 OK.
- 다만 부팅 직후 `airspy_list_devices()`가 비어있을 수 있어 첫 기동
  실패 후 5~10초 후 재시도가 필요. `RestartSec=10` 권장.

### 1.5 PPM/AGC/패킹 옵션 (Codex가 머지한 PR #22, #23 영향)

PR #22, #23(Codex 리뷰 반영)으로 다음이 추가됨:
- 12-bit USB packing (`--packing` / `packing: true`) — 6 MSPS(Mini) /
  10 MSPS(R2) 운용 시 USB 대역폭 ~33% 절감. **Pi 5의 USB 3.0이 안정
  공급되더라도 USB 허브를 거치는 경우 packing을 켜는 것이 안전.**
- LNA/Mixer AGC 토글 — 현장 RF 환경 변동 큰 사이트에서 권장.
- 소프트웨어 PPM 보정 — 하드웨어 미지원이므로 LO 사전 스케일.
  **TDOA 정확도에 직접 영향**, 노드별 측정값 적용 필요.

이 옵션들은 본 PR의 `rpi/thriftyx-capture.cfg.example`에 기본값으로
노출시켜 현장 운용자가 한 곳에서 조정할 수 있도록 한다.

---

## 2) 운영 환경 위험 (Codex 초안 보강)

### 2.1 발열/스로틀링 (신규)

- Pi 5는 80℃에서 클럭 스로틀링이 발생. Airspy 12-bit 6 MSPS 캡처는
  CPU 부하 ≈ 60~80%(추정), 케이스 내 권장: **공식 액티브 쿨러 또는
  방열판 + 팬**.
- 모니터링 항목: `vcgencmd measure_temp`, `vcgencmd get_throttled`.
  런북에 포함.

### 2.2 전원

- Pi 5는 공식 27W (5V/5A) USB-C PD 어댑터 사용 시 USB 포트 당
  1.6 A까지 공급. **그 외 어댑터는 600 mA 제한** → Airspy R2(특히
  bias-tee on)에서 USB error 빈발 가능.
- `/boot/firmware/config.txt`에 `usb_max_current_enable=1` 명시 권장.

### 2.3 시간 동기화

- TDOA는 노드 시각 정확도가 핵심. `chrony`로 변경(기본 `systemd-timesyncd`
  보다 jitter 낮음). 무인 노드에서는 NTP 동기화 완료 전 캡처 시작을
  피하기 위해 capture 서비스에 `After=time-sync.target`,
  `Wants=time-sync.target` 지정. (구버전 `rpi/ntp-after-online.*`은
  `systemd-time-wait-sync` 또는 chrony로 대체.)

### 2.4 스토리지 정책

- 마이크로SD 단독 운용 금지(권장). USB SSD를 `/var/lib/thriftyx`에
  마운트.
- 회전 삭제: `systemd-tmpfiles` 또는 cron으로 N일 초과 파일 정리.
- 디스크 80%/90% 임계값 알림은 `node_exporter` 또는 단순 cron 스크립트로
  구현. (런북 참조)

---

## 3) 본 PR의 산출물 (P0 완료)

| 산출물 | 경로 | 상태 |
|---|---|---|
| Pi 5 전용 설치 가이드 | `rpi/installation_pi5.md` | **추가** |
| systemd 캡처 서비스 템플릿 | `rpi/systemd/thriftyx-capture@.service` | **추가** |
| systemd 환경설정 예시 | `rpi/systemd/thriftyx-capture@.env.example` | **추가** |
| 캡처 설정 예시(Airspy Mini/R2) | `rpi/thriftyx-capture.cfg.example` | **추가** |
| 디스크 정리 cron 예시 | `rpi/cleanup_old_captures.sh` | **추가** |
| 운영 런북 | `docs/rpi5_runbook_ko.md` | **추가** |

이로써 Codex 초안 §3 "P0 — 배포 가능 상태 확보"는 모두 산출물 형태로
저장소에 반영되었다. 검증 체크리스트(`docs/rpi5_validation_checklist_ko.md`)는
PR #25에서 이미 머지됨.

---

## 4) 잔여 권장 작업 (P1 — 후속 PR)

1. **캡처 루프 FFT 가속** — `airspy_capture.py`에서 `np.fft.fft`를
   `signal_utils.compute_fft`로 교체하거나 pyfftw plan 재사용.
   (§1.2)
2. **flush 주기화** — 탐지마다 flush 대신 N블록/N초 단위 flush.
   (§1.3)
3. **헬스체크/하트비트** — 노드 → 서버 1분 주기 ping (디스크 사용률,
   드롭 샘플 수, 마지막 탐지 시각). 최소 스키마는 런북 부록 참조.
4. **24시간 soak test 자동화 스크립트** — duration 24h 캡처 + 로그
   회전 + 종료 코드 수집.
5. **원격 업데이트** — `git pull && systemctl restart` 의 멱등 wrapper.

---

## 5) "문제없이 돌아간다"의 합격 기준 (변경 없음)

1. 기능: capture → detect → (선택) identify/match/tdoa/pos 파이프라인 정상.
2. 안정성: 24시간 무인 동작, 자동복구 1회 이상 검증.
3. 운영성: 로그/알림/원격접속/복구절차 문서화 완료.

본 PR로 (3)을 1차 완성, (1)/(2)는 실기 검증 단계로 이동.

---

## 부록 A — Codex 머지 분석 요약

| PR | 머지 내용 | Pi5 관점 추가 권고 |
|---|---|---|
| #25 | Pi5 배포 리포트 + 검증 체크리스트(KR) | 본 문서로 보강 — P0 산출물 추가, 코드 레벨 리스크 추가 |
| #23 | AGC/PPM/packing/legacy C dead code 제거 | `rpi/thriftyx-capture.cfg.example`에 기본값 노출 |
| #22 | Codex+senior review parity 수정 | HAL 안정성 향상; 본 PR 영향 없음 |
| #20 | Airspy block index parity | 시계열/타임스탬프 무결성 — 24h soak 시 검증 |
| #18,19 | gain 기본값 7/7/7, 4건 정확성 버그 | `cfg.example`의 gain 기본값에 반영 |
| #15,16 | HAL block_size/sample type/persistent streaming | Pi5 USB 안정성에 긍정적 |
