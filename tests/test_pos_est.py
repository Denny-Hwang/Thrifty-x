import io
import itertools
import sys

import numpy as np
import pytest

from thriftyx import pos_est
from thriftyx import tdoa_est
from thriftyx.exceptions import ConfigError

SPEED_OF_LIGHT = pos_est.SPEED_OF_LIGHT

RX_POS = {
    0: [0, 10],
    1: [100, 0],
    2: [0, 100],
    3: [110, 90],
}

TX_POS = [30, 40]


def gen_tdoa_data(rx_pos, tx_pos):
    tdoas = []
    for rx0, rx1 in itertools.combinations(rx_pos.keys(), 2):
        rx0_pos = np.array(rx_pos[rx0])
        rx1_pos = np.array(rx_pos[rx1])
        t0 = np.linalg.norm(rx0_pos - tx_pos)
        t1 = np.linalg.norm(rx1_pos - tx_pos)
        tdoa = (t0 - t1) / SPEED_OF_LIGHT
        tdoas.append((rx0, rx1, tdoa, 0, 0, 0, 0))
    return np.array(tdoas, dtype=tdoa_est.TDOA_DTYPE)


def test_solve_numerically():
    tdoa_array = gen_tdoa_data(RX_POS, TX_POS)
    position, residual = pos_est.solve_numerically(tdoa_array, RX_POS)
    np.testing.assert_allclose(position, TX_POS, atol=1.0)
    assert residual < 1.0


def test_dop():
    tdoa_array = gen_tdoa_data(RX_POS, TX_POS)
    rx_pairs = list(zip(tdoa_array['rx0'], tdoa_array['rx1'], strict=True))
    dop_val = pos_est.dop(TX_POS, RX_POS, rx_pairs)
    assert dop_val > 0


def test_1d_dop():
    tx_pos = [5]
    rx_pos = {0: [0], 1: [10]}
    rx_pairs = [(0, 1)]
    assert pos_est.dop(tx_pos, rx_pos, rx_pairs) == 0.5


def _row(rx0, rx1, tdoa):
    return np.array([(rx0, rx1, tdoa, 5.0, 1.0, 0, 0)],
                    dtype=tdoa_est.TDOA_DTYPE)


def test_solve_1d_takes_receivers_from_the_tdoa_row():
    """Regression: solve_1d took rx0/rx1 from pos-rx.cfg's line order, so
    a config listing receiver 1 first mirrored every position about the
    midpoint (1100 m for a tag at 400 m)."""
    tx = 400.0
    for rx_pos in ({0: [0.0], 1: [1500.0]}, {1: [1500.0], 0: [0.0]},
                   {0: [1500.0], 1: [0.0]}, {1: [0.0], 0: [1500.0]}):
        tdoa = (abs(tx - rx_pos[0][0]) - abs(tx - rx_pos[1][0])) / SPEED_OF_LIGHT
        (x,), _ = pos_est.solve_1d(_row(0, 1, tdoa), rx_pos)
        assert x == pytest.approx(tx), rx_pos


def test_solve_1d_rejects_unknown_receiver():
    with pytest.raises(pos_est.EstimationError, match=r'\(0, 2\)'):
        pos_est.solve_1d(_row(0, 2, 0.0), {0: [0.0], 1: [10.0]})


@pytest.mark.parametrize('offset', [(0, 0), (12000, 0), (-12000, 0),
                                    (500000, 4000000)])
def test_solve_numerically_far_from_the_origin(offset):
    """Regression: the solver always started at (0.1, 0.1), outside the
    bounds (receivers +- 10 km) once the receivers were more than 10 km
    from the origin -- e.g. UTM coordinates -- and scipy raised
    ValueError, aborting `pos`."""
    rx_pos = {k: list(np.add(v, offset)) for k, v in RX_POS.items()}
    tx_pos = np.add(TX_POS, offset)
    tdoa_array = gen_tdoa_data(rx_pos, tx_pos)
    position, _ = pos_est.solve_numerically(tdoa_array, rx_pos)
    np.testing.assert_allclose(position, tx_pos, atol=0.01)


def test_underdetermined_names_the_receivers_needed():
    """x y z coordinates with the documented minimum of 3 receivers."""
    rx_pos = {0: [0, 0, 2], 1: [1200, 0, 3], 2: [0, 1000, 2.5]}
    tdoa_array = gen_tdoa_data(rx_pos, [300, 400, 1])
    with pytest.raises(pos_est.EstimationError,
                       match='3-D position needs TDOAs from at least 4 '
                             'receivers, got 3'):
        pos_est.solve_numerically(tdoa_array, rx_pos)


@pytest.mark.parametrize('text', ['0: 0 0\n1: 10\n', '0: 0 zero\n', '',
                                  '0: 1 2 3 4\n'])
def test_coordinates_must_be_consistent(text):
    stream = io.StringIO(text)
    stream.name = 'pos-rx.cfg'
    with pytest.raises(ConfigError, match='pos-rx.cfg'):
        tdoa_est.load_pos_config(stream)


def test_pos_on_a_one_row_tdoa_file(tmp_path, monkeypatch):
    """Regression: np.loadtxt squeezed a one-row .tdoa file (a 2-receiver
    run with a single match) to a 0-d array, and pos crashed iterating
    over it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'one.tdoa').write_text(
        '0 10.000000 3 0 1 16.678 30.0 0.9 100 200\n')
    (tmp_path / 'pos-rx.cfg').write_text('0: 0\n1: 10\n')
    monkeypatch.setattr(sys, 'argv', ['pos', 'one.tdoa', '-o', 'one.pos'])
    pos_est._main()
    positions = pos_est.load_positions('one.pos')
    assert positions.shape == (1,)
    assert positions['tx'][0] == 3
    assert positions['x'][0] == pytest.approx(7.5, abs=0.01)
