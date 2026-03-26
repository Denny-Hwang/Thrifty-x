# Thrifty-X

**Airspy-based TDOA positioning system for wildlife tracking.**

Thrifty-X is a derivative of [Thrifty](https://github.com/swkrueger/Thrifty)
by Schalk Willem Krüger (North-West University), extended to support
[Airspy Mini](https://airspy.com/airspy-mini/) and
[Airspy R2](https://airspy.com/airspy-r2/) SDR hardware.

## What's Changed from Original Thrifty

| Aspect | Original Thrifty | Thrifty-X |
|--------|-----------------|-----------|
| SDR Hardware | RTL-SDR (8-bit, 2.4 MSPS) | Airspy Mini (12-bit, 3/6 MSPS), Airspy R2 (12-bit, 2.5/10 MSPS) |
| Python Version | 2.7 / early 3 | 3.10+ with type hints |
| ADC Resolution | 8-bit unsigned | 12-bit signed |
| Gain Control | Single tuner_gain | LNA + Mixer + VGA (3-stage) |
| C Library | fastcard (librtlsdr) | fastcapture (libairspy) |
| Visualization | GnuRadio/osmosdr | matplotlib |
| Packaging | setup.py only | pyproject.toml + setup.py |

**All signal processing algorithms are preserved exactly** — carrier detection
(Dirichlet kernel interpolation), SoA estimation (Gaussian interpolation),
TDOA clock correction, and Levenberg-Marquardt position solving are unchanged.

## Requirements

- [Python](https://www.python.org/) 3.10+
- [NumPy](https://numpy.org/) >= 1.23
- [SciPy](https://scipy.org/) >= 1.9
- [Optional] [matplotlib](https://matplotlib.org/) for analysis and visualization
- [Optional] [libairspy](https://github.com/airspy/airspyone_host) for live capture

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

## Usage

The CLI workflow is identical to the original Thrifty:

```bash
# On each receiver:
thriftyx capture rx0.card
thriftyx detect rx0.card -o rx0.toad

# On server:
thriftyx identify rx0.toad rx1.toad
thriftyx match
thriftyx tdoa
thriftyx pos
```

The legacy `thrifty` command also works as an alias.

### Airspy-Specific Options

```bash
thriftyx capture rx0.card --device-type airspy_mini \
    --sample-rate 6M --center-freq 433.95M \
    --lna-gain 5 --mixer-gain 5 --vga-gain 5 --bias-tee
```

### Using Existing RTL-SDR Data

Existing .card files captured with the original Thrifty (v1 format, 8-bit)
are automatically detected and processed correctly:

```bash
thriftyx detect old_rtlsdr_data.card -o detections.toad
```

## Supported Hardware

| Device | Sample Rates | Frequency Range | ADC |
|--------|-------------|-----------------|-----|
| Airspy Mini | 3 / 6 MSPS | 24 – 1800 MHz | 12-bit |
| Airspy R2 | 2.5 / 10 MSPS | 24 – 1800 MHz | 12-bit |

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

## Publications & Attribution

Thrifty-X is built upon the work described in:

> Krüger, S.W. (2016). *An inexpensive hyperbolic positioning system for
> tracking wildlife using off-the-shelf hardware.* Master's dissertation,
> North-West University, Potchefstroom Campus.
> [https://hdl.handle.net/10394/25449](https://hdl.handle.net/10394/25449)

```bibtex
@mastersthesis{kruger2016inexpensive,
  title={An inexpensive hyperbolic positioning system for tracking wildlife
         using off-the-shelf hardware},
  author={Kr{\"u}ger, Schalk Willem},
  year={2016},
  school={North-West University (South Africa), Potchefstroom Campus}
}
```

Original Thrifty source: [github.com/swkrueger/Thrifty](https://github.com/swkrueger/Thrifty)

## License

This project is licensed under the GNU General Public License v3.0 — see
[LICENSE.txt](LICENSE.txt) for details.

Thrifty-X is a derivative work of Thrifty. Both the original and this
derivative are distributed under the same GPL-3.0 license.
