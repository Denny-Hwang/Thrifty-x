/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/* `fastdet -x -` writes the card to stdout, so the status lines must go
 * to stderr: mixed into the card they made it unreadable to both card
 * readers (fastdet --card and thriftyx detect).  Runs the fastdet binary
 * given as argv[1] on a synthetic recording and reads its card back. */

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <regex>
#include <string>
#include <vector>

#include <sys/wait.h>

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", \
                __FILE__, __LINE__, #cond); \
        failures++; \
    } \
} while (0)

enum { BLOCK = 1024, HISTORY = 128, CHIPS = 24, CHIP_LEN = 4,
       TEMPLATE = CHIPS * CHIP_LEN, BLOCKS = 6, BIN = 40, BURST = 300 };

// The template is CHIPS pseudo-random +-1 chips of CHIP_LEN samples.
// The recording keys a carrier at FFT bin BIN on and off with the same
// chips, starting BURST samples into each block's new data, over a
// little noise.
static void write_inputs() {
    uint32_t seed = 20260924;
    auto next = [&seed]() {
        seed = seed * 1103515245u + 12345u;
        return (seed >> 16) & 0x7fff;
    };
    std::vector<float> tpl(TEMPLATE);
    for (int chip = 0; chip < CHIPS; ++chip) {
        float value = (next() & 1) ? 1.0f : -1.0f;
        for (int i = 0; i < CHIP_LEN; ++i) {
            tpl[chip * CHIP_LEN + i] = value;
        }
    }
    std::ofstream tpl_file("card_stdout.tpl", std::ios::binary);
    uint16_t count = TEMPLATE;
    tpl_file.write((const char*)&count, sizeof(count));
    tpl_file.write((const char*)tpl.data(), tpl.size() * sizeof(float));

    const size_t new_len = BLOCK - HISTORY;
    std::vector<int16_t> iq(2 * BLOCKS * new_len);
    for (size_t i = 0; i < BLOCKS * new_len; ++i) {
        size_t at = i % new_len - BURST;  // wraps outside the burst
        double amp = (at < TEMPLATE && tpl[at] > 0) ? 12000 : 0;
        double phase = 2 * M_PI * BIN * (double)i / BLOCK;
        iq[2 * i] = (int16_t)lround(amp * cos(phase)
                                    + (int)(next() % 41) - 20);
        iq[2 * i + 1] = (int16_t)lround(amp * sin(phase)
                                        + (int)(next() % 41) - 20);
    }
    std::ofstream raw("card_stdout.raw", std::ios::binary);
    raw.write((const char*)iq.data(), iq.size() * sizeof(int16_t));
}

static int run(const std::string& command) {
    int status = std::system(command.c_str());
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

static std::vector<std::string> read_lines(const char* path) {
    std::vector<std::string> lines;
    std::ifstream file(path);
    for (std::string line; std::getline(file, line); ) {
        lines.push_back(line);
    }
    return lines;
}

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <fastdet binary>\n", argv[0]);
        return 2;
    }
    const std::string fastdet = std::string("'") + argv[1] + "'";
    const std::string geometry = " -b 1024 -h 128 -k 0 -z card_stdout.tpl";
    write_inputs();

    // The card goes to stdout, the status lines to stderr.
    CHECK(run(fastdet + " -i card_stdout.raw" + geometry
              + " -x - >card_stdout.card 2>card_stdout.log") == 0);
    std::vector<std::string> card = read_lines("card_stdout.card");
    CHECK(!card.empty() && card[0].rfind("#v2 ", 0) == 0);
    const std::regex data_line("[0-9]+\\.[0-9]{6} [0-9]+ [A-Za-z0-9+/]+=*");
    const size_t data_len = (4 * BLOCK + 2) / 3 * 4;  // base64 of a block
    int blocks = 0;
    for (const std::string& line : card) {
        if (line.rfind("#", 0) == 0) {
            continue;
        }
        if (!std::regex_match(line, data_line)
                || line.size() - line.rfind(' ') - 1 != data_len) {
            fprintf(stderr, "not a card line: '%.60s'\n", line.c_str());
            failures++;
            continue;
        }
        ++blocks;
    }
    CHECK(blocks == BLOCKS);
    int status_lines = 0;
    for (const std::string& line : read_lines("card_stdout.log")) {
        status_lines += line.rfind("block #", 0) == 0;
    }
    CHECK(status_lines == BLOCKS);

    // Both card readers must take it; fastdet finds the same bursts.
    CHECK(run(fastdet + " -q --card -i card_stdout.card" + geometry
              + " -o - >card_stdout.toad") == 0);
    CHECK((int)read_lines("card_stdout.toad").size() == BLOCKS);

    // The .toad and the card cannot share stdout.
    CHECK(run(fastdet + " -q -i card_stdout.raw" + geometry
              + " -o - -x - >/dev/null 2>&1") == 64);

    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("card stdout tests passed\n");
    return 0;
}
