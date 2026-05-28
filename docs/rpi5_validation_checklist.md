# Raspberry Pi 5 Deployment Validation Checklist (RX Site)

Date: 2026-05-06

## 1) Prerequisites
- [ ] Raspberry Pi OS 64-bit installation complete
- [ ] System update complete (`apt update && apt upgrade`)
- [ ] Airspy connection/power/antenna configuration complete
- [ ] Repository clone and Python venv setup complete

## 2) Installation Validation
- [ ] `libairspy` installation complete
- [ ] `pip install -e .[all]` succeeded
- [ ] `python -c "import thriftyx"` succeeded
- [ ] Airspy serial enumeration succeeded

## 3) Functional Validation
- [ ] `thriftyx capture ...` ran successfully for 60 seconds
- [ ] `thriftyx detect ...` output generation succeeded
- [ ] Output file size/count meets expectations

## 4) Operational Validation
- [ ] systemd service registration/start succeeded
- [ ] Automatic startup after reboot succeeded
- [ ] Automatic restart after failure (process kill) succeeded
- [ ] Log collection/lookup (journalctl) available

## 5) Stability Validation
- [ ] 2 hours of continuous operation succeeded
- [ ] 24-hour soak test succeeded (recommended)
- [ ] No memory usage spike/leak
- [ ] Sample drop/loss rate within tolerance

## 6) Storage/Recovery Validation
- [ ] Warning confirmed when disk is 80% or higher
- [ ] Cleanup policy operates when disk is 90% or higher
- [ ] Pipeline recovery after network disconnection/recovery

## 7) Decision
- [ ] Go (field deployment possible)
- [ ] Conditional Go (conditional deployment)
- [ ] No-Go (revalidate after improvement)

## 8) Record Template

| Item | Result (Pass/Fail) | Evidence (log/screenshot/file) | Notes |
|---|---|---|---|
| Installation Validation |  |  |  |
| Functional Validation |  |  |  |
| Operational Validation |  |  |  |
| Stability Validation |  |  |  |
| Storage/Recovery Validation |  |  |  |
