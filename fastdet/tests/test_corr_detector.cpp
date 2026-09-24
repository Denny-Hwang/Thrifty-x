/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* Correlator edge cases.  A block_history of template_len - 1 or
 * template_len (both accepted) puts the peak search window at the ends
 * of the correlation, where the peak has one neighbour; interpolation
 * used to read outside corr_power_ there.  A template with no samples
 * used to size the correlation past the end of the IFFT output. */

#include <complex>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "corr_detector.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

static void test_interpolation_needs_both_neighbours() {
    // buf[0] and buf[4] are outside the three values: were they read,
    // the huge value would pull the offset to a clip bound.
    float buf[] = {1e6f, 4, 2, 1, 1e6f};
    float* power = buf + 1;
    CHECK(CorrDetector::interpolate_gaussian(power, 3, 0) == 0);
    CHECK(CorrDetector::interpolate_gaussian(power, 3, 2) == 0);
    CHECK(CorrDetector::interpolate_gaussian(power, 1, 0) == 0);
    // Powers 1, 4, 2: log amplitudes 0, b, b/2 give an offset of 1/6.
    float peak[] = {1, 4, 2};
    double offset = CorrDetector::interpolate_gaussian(peak, 3, 1);
    CHECK(offset > 0.1666 && offset < 0.1667);
}

// Correlate a block holding only the template, at `start`.
static CorrDetection detect_template_at(size_t start, size_t history) {
    const size_t block = 256;
    std::vector<float> tpl(31);
    uint32_t seed = 7;
    for (float& t : tpl) {
        seed = seed * 1103515245u + 12345u;
        t = ((seed >> 16) & 1) ? 1.0f : -1.0f;
    }
    CorrDetector det(tpl, block, history, 0, 1);

    FFT fft(block, true);  // FFTW_MEASURE: fill the input after planning
    for (size_t i = 0; i < block; ++i) {
        fft.input()[i].real = 0;
        fft.input()[i].imag = 0;
    }
    for (size_t i = 0; i < tpl.size(); ++i) {
        fft.input()[start + i].real = tpl[i];
    }
    fft.execute();
    float energy = tpl.size();  // sum of |x|^2
    return det.detect((const std::complex<float>*)fft.output(), energy);
}

static void test_peak_at_window_edges() {
    // history == template_len: the window starts at correlation index 0.
    CorrDetection first = detect_template_at(0, 31);
    CHECK(first.detected);
    CHECK(first.peak_idx == 0);
    CHECK(first.peak_offset == 0);
    // history == template_len - 1: it also ends at the last index,
    // 256 - 31 (the template's last possible start).
    CorrDetection last = detect_template_at(256 - 31, 30);
    CHECK(last.detected);
    CHECK(last.peak_idx == 256 - 31);
    CHECK(last.peak_offset == 0);
    // And inside the window.
    CorrDetection mid = detect_template_at(100, 30);
    CHECK(mid.detected && mid.peak_idx == 100);
}

static void write_tpl(const char* path, uint16_t count,
                      const std::vector<float>& samples) {
    std::ofstream f(path, std::ios::binary);
    f.write((const char*)&count, sizeof(count));
    f.write((const char*)samples.data(), samples.size() * sizeof(float));
}

static bool load_fails(const char* path, const char* expected) {
    try {
        load_template(path);
    } catch (std::runtime_error& e) {
        if (std::string(e.what()).find(expected) == std::string::npos) {
            fprintf(stderr, "unexpected error: %s\n", e.what());
            return false;
        }
        return true;
    }
    return false;
}

static void test_empty_template_is_refused() {
    write_tpl("zero.tpl", 0, {});
    CHECK(load_fails("zero.tpl", "sample count is 0"));
    write_tpl("zero_padded.tpl", 0, {0, 0, 0, 0});  // a zero-filled file
    CHECK(load_fails("zero_padded.tpl", "sample count is 0"));
    write_tpl("truncated.tpl", 4, {1, 2});
    CHECK(load_fails("truncated.tpl", "'truncated.tpl': the file is truncated"));
    CHECK(load_fails("missing.tpl", "'missing.tpl': No such file"));

    write_tpl("three.tpl", 3, {1, -1, 0.5f});
    std::vector<float> three = load_template("three.tpl");
    CHECK(three.size() == 3 && three[1] == -1 && three[2] == 0.5f);

    bool refused = false;
    try {
        CorrDetector det(std::vector<float>(), 256, 16, 0, 15);
    } catch (std::runtime_error&) {
        refused = true;
    }
    CHECK(refused);
}

int main() {
    test_interpolation_needs_both_neighbours();
    test_peak_at_window_edges();
    test_empty_template_is_refused();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("corr_detector tests passed\n");
    return 0;
}
