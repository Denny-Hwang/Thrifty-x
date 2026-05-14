# Phase A: 코드 정적 분석 결과

> 작업 디렉토리: `/home/user/Thrifty-x`
> 분석 대상: `thriftyx/hal/airspy_*.py`, `thriftyx/block_data.py`,
> `thriftyx/airspy_capture.py`, `fastcapture/airspy_reader.c`

## 샘플 타입 설정 체인

| 단계 | 파일:라인 | 코드 | 비고 |
|------|----------|------|------|
| 상수 정의 (Py) | `thriftyx/hal/airspy_mini.py:169-172` | `AIRSPY_SAMPLE_FLOAT32_IQ = 0`, `AIRSPY_SAMPLE_FLOAT32_REAL = 1`, `AIRSPY_SAMPLE_INT16_IQ = 2`, `AIRSPY_SAMPLE_INT16_REAL = 3` | libairspy 표준 enum 값과 일치 |
| `airspy_set_sample_type` 바인딩 | `thriftyx/hal/airspy_mini.py:128-129` | `_lib.airspy_set_sample_type.argtypes = [ctypes.c_void_p, ctypes.c_int]` | restype=c_int |
| Python HAL set_sample_type 호출 | `thriftyx/hal/airspy_mini.py:361-364` | `_lib.airspy_set_sample_type(self._handle, ctypes.c_int(AIRSPY_SAMPLE_INT16_IQ))` | **호출됨, INT16_IQ(=2)로 설정**. 단, 반환 코드가 0이 아니면 **`logger.warning`만 띄우고 계속 진행** — 실패 시 libairspy 기본값 FLOAT32_IQ로 동작할 수 있음 |
| C fastcapture set_sample_type 호출 | `fastcapture/airspy_reader.c:201-202` | `airspy_set_sample_type(state->device, AIRSPY_SAMPLE_INT16_IQ)` | 실패하면 `goto err`로 즉시 중단(fail-fast) |
| Python 콜백 데이터 해석 | `thriftyx/hal/airspy_mini.py:656-659` | `buf = (ctypes.c_int16 * count).from_address(...)`, `arr = np.frombuffer(buf, dtype=np.int16).copy()` | int16으로 해석 |
| C 콜백 데이터 해석 | `fastcapture/airspy_reader.c:50-54` | `int16_t *src = (int16_t *)transfer->samples;` | int16으로 해석 |
| `raw_to_complex` 입력 가정 | `thriftyx/block_data.py:55-94` | `bit_depth==12`이면 `np.int16`로 dtype 강제, `floats / 32768.0`로 정규화 | **`/32768.0` (FULL int16 범위 가정), NOT `/2048.0` (12-bit raw)** |
| 나누기 상수 | `thriftyx/block_data.py:84` | `floats = floats / 32768.0` | libairspy가 12-bit ADC를 좌측 시프트하여 full int16 범위로 출력한다는 가정 (`airspy_reader.c:50` 주석 "scaled to full int16 range by libairspy"와 일치) |
| .card 파일 저장 dtype | `thriftyx/airspy_capture.py:476, 489-491` + `block_data.py:236` | `block_raw = np.concatenate([history_raw, raw])`는 int16, `base64.b64encode(raw.tobytes())` | **v2 .card는 base64(int16 interleaved I/Q)**, 헤더 `#v2 bit_depth=12 sample_rate=…` 포함 |

## 캡처 경로

- [x] Python HAL (`thriftyx/hal/airspy_mini.py`) — **`thriftyx capture` CLI가 사용하는 경로**
  - `thriftyx/airspy_capture.py:332` → `from thriftyx.hal.device_factory import create_device`
  - `_capture_airspy()`가 device_type ∈ {`airspy_mini`, `airspy_r2`}일 때 호출됨 (`airspy_capture.py:609-613`)
  - `device.read_sync()`로 int16 IQ pair 동기 수신
- [ ] C fastcapture (`fastcapture/airspy_reader.c`) — **컴파일 가능하지만 `thriftyx capture`는 Airspy에 대해 호출하지 않음**.
  RTL-SDR 경로(`_capture_rtlsdr_fastcard`, `airspy_capture.py:144-201`)만 외부 `fastcard` 바이너리를 사용.
- 결론: **R2 캡처의 실제 경로는 100% Python HAL**.

## 불일치 발견

코드 경로만 보면 **표면적으로는 불일치 없음**. 세 가지 층(libairspy 설정, 콜백 dtype, raw_to_complex 정규화)이 모두 INT16_IQ를 가정한다.

다만 다음 두 가지 **잠재적 약점**이 발견됨:

1. **`airspy_set_sample_type()` 실패 시 silent fallback** (`airspy_mini.py:361-364`):
   ```python
   ret = _lib.airspy_set_sample_type(
       self._handle, ctypes.c_int(AIRSPY_SAMPLE_INT16_IQ))
   if ret != 0:
       logger.warning("airspy_set_sample_type() failed: %d", ret)
   ```
   호출이 실패하면 경고만 띄우고 libairspy 기본값(=`FLOAT32_IQ`, 값 0)을 그대로 사용한다.
   이 경우 콜백에 들어오는 데이터는 `float32` 인데 코드는 `int16`으로 reinterpret한다 → IQ가 4 byte float 단위로 정렬되어 있는데 2 byte int16 단위로 잘려서 해석되므로, **스펙트럼이 완전히 깨진다**. 이중-bin / SNR 급락의 직접 후보.

2. **`set_packing(True)`이 sample_type을 리셋할 가능성** (`airspy_mini.py:618-633` + `airspy_capture.py:385-386`):
   - libairspy의 `airspy_set_packing()` 구현이 일부 빌드에서 sample type 내부 상태를 건드리는 사례가 보고되어 있음.
   - R2 10 MSPS에서 USB 2.0 호스트인 경우 `packing: true`가 설정에 들어가 있을 수 있음. 다만 예제 cfg(`example/detector_r2.cfg`)에는 `packing` 키가 명시되어 있지 않으므로 기본값(=`False`)이 사용될 것으로 보임 → 이 경로는 가능성 낮음.

3. **`apply_gain_mode`가 `combined`/AGC 경로일 때 sample_type 재설정 안 함**: open() 직후 한 번만 설정한 sample_type이 이후 어떤 API 호출에도 건드려지지 않는다고 가정. libairspy 1.0.x 일부 빌드에서는 `airspy_set_samplerate` 직후 sample type이 기본값으로 리셋되는 사례가 있다고 알려져 있음 (확인 필요).

## 예상 시나리오

- [ ] 시나리오 1: `set_sampletype` 호출 없음 → libairspy 기본값 FLOAT32_IQ 사용 → HAL은 INT16으로 해석 → 포맷 불일치
  → **반증됨**. `airspy_mini.py:361`과 `airspy_reader.c:201` 모두 명시적으로 INT16_IQ를 호출함.
- [x] **시나리오 1-bis (변형)**: `set_sample_type` **호출은 있으나 반환값이 비-0(실패)인데 silent하게 무시됨** → libairspy 기본값 FLOAT32_IQ 그대로 사용 → 콜백 데이터(실제는 float32)를 int16로 reinterpret → **이중 bin + SNR 14 dB의 가장 유력한 시나리오**.
- [ ] 시나리오 2: `set_sampletype(INT16_IQ)` 호출 있음 → 콜백에서 dtype 해석 오류
  → **반증됨**. Python·C 양쪽 콜백 모두 `int16`로 명시적으로 해석.
- [ ] 시나리오 3: `set_sampletype(FLOAT32_IQ)` 호출 있음 → `raw_to_complex`에서 `/2048.0` 잘못 적용
  → **반증됨**. 코드는 `/32768.0`. 만약 데이터가 진짜 float32였다면 `/2048.0`도 `/32768.0`도 다 틀린 결과(컬렉션 자체가 손상).
- [ ] 시나리오 4: C fastcapture 경로가 별도 처리 → Python HAL과 다른 동작
  → **반증됨**. `thriftyx capture`는 Airspy에 대해 C fastcapture를 호출하지 않음.
- [x] **시나리오 5 (보조 가설)**: `airspy_set_packing(True)` 또는 `airspy_set_samplerate()`가 일부 libairspy 빌드에서 sample_type 상태를 리셋함 → 결과적으로 시나리오 1-bis와 같은 증상.
  - 확인 방법: 런타임에 libairspy 빌드 버전을 출력하고(이미 `libairspy_version()` 존재, `airspy_mini.py:195-212`), `set_sample_type` 반환값을 `WARNING` 대신 **`raise`** 시켜 fail-fast로 만들어 동일 환경에서 재현되는지 확인.
- [ ] 시나리오 6 (외부 요인): R2 LO 누설/DC 오프셋이 정상보다 큼 → bin 0 근처에 강한 DC + 실제 캐리어 = 이중 bin 처럼 보임.
  → **부분적으로만 그럴듯**. 정상 운영에서 단일 bin이 관측된다는 RTL-SDR 비교가 있으므로 R2에 한정된 DC 누설일 수 있음. 다만 SNR이 40→14 dB로 급락하는 정도라면 단순 DC 누설을 넘어선 포맷 손상에 가까움.

## 결정 노트

Python HAL의 `set_sample_type` 반환값을 무시하는 패턴이 **현 시점에서 가장 강한 단일 원인 후보**다. 다만 코드만으로는 R2에서 실제로 그 분기가 trigger되었는지 단정할 수 없으므로, Phase B에서 .card 파일의 raw 바이트를 직접 해석하여 어느 dtype 가정이 사리에 맞는 값을 내는지 확인해야 한다.
