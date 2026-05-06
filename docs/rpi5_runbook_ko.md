# Raspberry Pi 5 RX 노드 운영 런북

본 문서는 Pi 5 + Airspy로 구성된 무인 RX 노드의 일상 점검과 장애 대응
절차를 정리한다. 설치 절차는 `rpi/installation_pi5.md`,
배포 분석/계획은 `docs/rpi5_deployment_report_ko.md` 참조.

---

## 1. 일상 점검 (주 1회)

```bash
# 서비스 상태
systemctl status thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 --since "24 hours ago" | tail -50

# 디스크
df -h /var/lib/thriftyx

# 시간 동기
chronyc tracking

# 발열 / 스로틀링
vcgencmd measure_temp
vcgencmd get_throttled       # 0x0 이면 정상

# Airspy 인식
python3 -c "from thriftyx.hal.airspy_mini import list_airspy_serials; print(list_airspy_serials())"
```

---

## 2. 장애 시나리오와 대응

### 2.1 캡처 서비스가 반복 재시작됨
- `journalctl -u thriftyx-capture@rx0 -n 200` 로 마지막 stack/에러 확인
- `airspy_open() failed`: USB 케이블/허브/전원 점검 → 교체 후
  `systemctl restart thriftyx-capture@rx0`
- `DeviceConfigError`: `capture.cfg`의 sample_rate가 장치 지원 범위인지
  확인 (Mini: 3M/6M, R2: 2.5M/10M)

### 2.2 디스크 부족
- cron `cleanup_old_captures.sh` 가 실행되었는지: `journalctl -t thriftyx-cleanup`
- 임시 조치: `find /var/lib/thriftyx/card -type f -mtime +1 -delete`
- 보존정책 변경: `/etc/default/thriftyx-capture@rx0` 에 `CARD_RETENTION_DAYS=N`

### 2.3 스로틀링/발열
- 정상: `get_throttled` = `0x0`
- 비트 16/17/18 set → 과거에 throttle 발생. 쿨러 청소/재장착, 케이스
  통풍 확보, 필요 시 `arm_freq` 약간 하향.

### 2.4 시간 동기 이상
- `chronyc tracking` 의 `Last offset` 가 ±10 ms 이상이면 NTP source
  문제. 노드 간 `chronyc sources -v` 비교.
- WAN 단절 시 일시적 free-run 허용. 복구 후 자동 재동기화.

### 2.5 노드와 서버 간 데이터 전송 실패
- rsync 로그 확인. `~/.ssh/known_hosts` 만료 여부.
- 네트워크 단절은 캡처 자체와 독립 — 캡처는 로컬에 계속 적재됨.

---

## 3. 24시간 soak 테스트 절차

```bash
sudo systemctl stop thriftyx-capture@rx0
source ~/thrifty-x/.venv/bin/activate

OUT=/var/lib/thriftyx/soak/$(date +%Y%m%dT%H%M%S)
mkdir -p "$OUT"

nohup thriftyx capture "$OUT/capture.card" \
    --config /var/lib/thriftyx/capture.cfg \
    --duration 86400 > "$OUT/stdout.log" 2> "$OUT/stderr.log" &

echo $! > "$OUT/pid"
```

24시간 후 합격 판정:
- 프로세스 종료코드 0
- `card` 파일 size 단조 증가, 손상 없음
- `vcgencmd get_throttled` = `0x0`
- 메모리 사용 안정 (peak vs end < 10% 차이)
- 드롭 샘플 카운트(있을 시) 허용치 이하

---

## 4. 헬스체크 / 하트비트 (P1 권장 — 후속 PR)

최소 스키마 제안 (HTTP POST JSON, 60초 주기):

```json
{
  "rxid": 0,
  "ts": "2026-05-06T12:34:56Z",
  "uptime_s": 123456,
  "disk_pct": 42,
  "cpu_temp_c": 58.3,
  "throttled": "0x0",
  "service_state": "active",
  "last_detection_ts": "2026-05-06T12:34:50Z",
  "dropped_samples": 0,
  "version": "0.1.0"
}
```

서버에서 60초 내 미수신 시 알림. 본 PR에서는 스키마만 정의.
구현은 별도 PR.

---

## 5. 원격 접속

기존 `rpi/installation.md`의 reverse SSH 섹션이 Pi 5에서도 그대로
유효(autossh + systemd). 다만 weaved 섹션은 deprecated — 무시할 것.

---

## 6. 업데이트 절차 (수동)

```bash
ssh rx0
cd ~/thrifty-x
git fetch origin
git log --oneline HEAD..origin/master    # 변경 확인
git pull --ff-only
source .venv/bin/activate
pip install -e ".[analysis,fft]"
sudo systemctl restart thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 -f
```

롤백: `git checkout <previous-sha>` 후 `pip install -e ...` 재실행
및 서비스 재시작.
