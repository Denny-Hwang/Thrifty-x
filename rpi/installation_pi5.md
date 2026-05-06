# Raspberry Pi 5 — Thrifty-X RX Site 설치 가이드

본 문서는 Raspberry Pi 5(64-bit Bookworm) + Airspy Mini/R2 조합으로
독립형 RX 노드를 구성하는 절차를 정리한다. 레거시 `rpi/installation.md`
(Pi 3 / Jessie / fastcard) 는 RTL-SDR 운용 시에만 참고한다.

---

## 1. 권장 하드웨어

| 항목 | 권장 사양 |
|---|---|
| 보드 | Raspberry Pi 5 (4 GB 이상) |
| 전원 | 공식 27W USB-C PD 어댑터 (5V/5A) |
| 저장 | 마이크로SD 32 GB(부팅) + USB SSD 128 GB↑ (`/var/lib/thriftyx`) |
| 냉각 | 공식 액티브 쿨러 또는 케이스 + 팬 (스로틀 방지) |
| SDR | Airspy Mini 또는 Airspy R2 |
| 케이블 | 짧고 양질의 USB 2.0/3.0 (USB 허브 사용 시 외부 전원 필요) |
| 시간 동기 | 인터넷(WAN) 또는 로컬 NTP/PTP 서버 |

---

## 2. OS 준비

```bash
# Raspberry Pi OS 64-bit (Bookworm) Lite 설치 후
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config         # Hostname=rx0, Locale, Timezone, Expand FS
```

`/boot/firmware/config.txt` 추가:

```
# USB 포트당 출력 1.6 A 허용 (공식 27W PD 어댑터 사용 시)
usb_max_current_enable=1
```

선택: HDMI/Bluetooth 비활성화로 전력 절약 (헤드리스 노드).

---

## 3. 패키지 설치

```bash
sudo apt install -y \
    python3 python3-venv python3-pip git \
    build-essential cmake pkg-config \
    libusb-1.0-0-dev \
    libfftw3-dev \
    airspy libairspy-dev \
    chrony

# (선택) 로그/모니터링
sudo apt install -y htop tmux rsync
```

> Bookworm `airspy` 패키지는 libairspy 1.0.10 이상으로,
> Thrifty-X HAL이 필요로 하는 `airspy_open_sn`,
> `airspy_list_devices`, `airspy_get_samplerates`,
> `airspy_set_packing` 을 모두 제공한다.

### 3.1 udev 규칙 (사용자 권한)

apt의 `airspy` 패키지가 `/lib/udev/rules.d/60-libairspy0.rules` 를
설치한다. 사용자 계정이 `plugdev` 그룹에 속해야 한다:

```bash
sudo usermod -aG plugdev $USER
# 재로그인 후
groups | grep plugdev
```

### 3.2 시간 동기화 (TDOA 핵심)

```bash
sudo systemctl disable --now systemd-timesyncd
sudo systemctl enable --now chrony
chronyc tracking         # 동기화 상태 확인
```

WAN이 없는 사이트는 인접 노드 중 하나를 chrony 서버로 설정.

---

## 4. Thrifty-X 설치

```bash
git clone https://github.com/Denny-Hwang/thrifty-x.git ~/thrifty-x
cd ~/thrifty-x
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[analysis,fft]"   # fft = pyfftw, analysis = matplotlib
```

### 4.1 설치 검증

```bash
python -c "import thriftyx, numpy, scipy; print(thriftyx.__version__)"
python -c "from thriftyx.hal.airspy_mini import list_airspy_serials; print(list_airspy_serials())"
thriftyx --help
```

장치 목록이 비어있으면 USB 연결/권한/`lsusb | grep Airspy` 확인.

---

## 5. 데이터 디렉터리

USB SSD를 `/var/lib/thriftyx`에 마운트하는 것을 권장한다(SD 카드 wear 회피).

```bash
sudo mkdir -p /var/lib/thriftyx/{card,toad,log}
sudo chown -R $USER:$USER /var/lib/thriftyx
```

`/etc/fstab` 예시 (UUID는 `lsblk -f`로 확인):

```
UUID=XXXX-XXXX  /var/lib/thriftyx  ext4  defaults,noatime,nofail  0  2
```

---

## 6. 캡처 설정

`rpi/thriftyx-capture.cfg.example`을 복사해서 사용:

```bash
cp ~/thrifty-x/rpi/thriftyx-capture.cfg.example /var/lib/thriftyx/capture.cfg
$EDITOR /var/lib/thriftyx/capture.cfg   # rxid, tuner_freq, gain 등 조정
```

수동 시험 캡처:

```bash
source ~/thrifty-x/.venv/bin/activate
thriftyx capture /var/lib/thriftyx/card/test.card \
    --config /var/lib/thriftyx/capture.cfg --duration 60
```

---

## 7. systemd 서비스 등록

```bash
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-capture@.service /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-capture@.env.example /etc/default/thriftyx-capture@rx0
sudo $EDITOR /etc/default/thriftyx-capture@rx0     # USER, paths 조정
sudo systemctl daemon-reload
sudo systemctl enable --now thriftyx-capture@rx0.service
journalctl -u thriftyx-capture@rx0 -f
```

재부팅 후 자동 기동 검증:

```bash
sudo reboot
# 부팅 후
systemctl status thriftyx-capture@rx0
```

---

## 8. 디스크 정리 (cron)

```bash
sudo cp ~/thrifty-x/rpi/cleanup_old_captures.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/cleanup_old_captures.sh
( crontab -l 2>/dev/null; echo "0 * * * * /usr/local/bin/cleanup_old_captures.sh" ) | crontab -
```

기본 정책: `/var/lib/thriftyx/card/` 7일 초과 파일 삭제,
`log/` 30일 초과 삭제. 환경변수로 조정 가능.

---

## 9. 검증 체크리스트

`docs/rpi5_validation_checklist_ko.md` 의 항목을 모두 통과해야 현장
배포 가능 상태로 판정한다. 필수 항목 1건이라도 실패하면 **No-Go**.

---

## 10. 트러블슈팅

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| `libairspy not available` | apt 패키지 미설치 / 잘못된 경로 | `sudo apt install airspy libairspy-dev`, `ldconfig -p \| grep airspy` |
| `airspy_open() failed: -2` | 권한/USB busy | plugdev 그룹, 다른 프로세스 점유 확인 (`lsof | grep airspy`) |
| 6/10 MSPS에서 USB error 빈발 | 전원/케이블/허브 | 27W PD + 짧은 직결 케이블, `--packing` 활성화 |
| 30~60초 후 throttling | 발열 | `vcgencmd get_throttled`, 액티브 쿨러 점검 |
| 노드 간 타임스탬프 불일치 | 시간 동기 | `chronyc tracking`, NTP source 확인 |
| 부팅 직후 서비스 1회 실패 | USB enumeration 지연 | `RestartSec=10`로 자동 재시도 (기본값) |
