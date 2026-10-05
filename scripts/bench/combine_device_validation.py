#!/usr/bin/env python3
"""Combine one receiver's validation CSVs into analysis-ready datasets."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

NAME_RE = re.compile(
    r"^(?P<device>.+)_phase(?P<phase>[ABC])_"
    r"(?P<kind>[A-Za-z0-9]+)"
    r"(?:_rate(?P<rate>[^_]+))?"
    r"(?:_rep(?P<rep>\d+))?"
    r"(?:_(?P<direction>asc|desc))?"
    r"(?:_tone_(?P<tone>[mp]\d+kHz))?"
    r"\.csv$"
)


def metadata(path: Path) -> dict[str, str]:
    match = NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"unexpected validation filename: {path.name}")
    data = match.groupdict(default="")
    return {
        "source_device": data["device"],
        "source_phase": data["phase"],
        "source_kind": data["kind"],
        "source_file": path.name,
        "source_rate_tag": data["rate"],
        "source_rep": data["rep"],
        "source_direction": data["direction"],
        "source_tone_tag": data["tone"],
    }


def read_file(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        return fields, list(reader)


def write_combined(
    out_path: Path,
    paths: list[Path],
) -> int:
    if not paths:
        return 0

    first_fields: list[str] | None = None
    records: list[dict[str, str]] = []
    source_fields = [
        "source_device",
        "source_phase",
        "source_kind",
        "source_file",
        "source_row",
        "source_rate_tag",
        "source_rep",
        "source_direction",
        "source_tone_tag",
    ]

    for path in paths:
        meta = metadata(path)
        fields, rows = read_file(path)
        if first_fields is None:
            first_fields = fields
        elif fields != first_fields:
            raise SystemExit(
                f"{path}: CSV columns differ from the first input file"
            )
        for index, row in enumerate(rows, 1):
            records.append(
                {
                    **meta,
                    "source_row": str(index),
                    **row,
                }
            )

    assert first_fields is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=source_fields + first_fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(records)
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--device-label", required=True)
    args = parser.parse_args()

    root = args.directory
    label = args.device_label
    files = sorted(root.glob(f"{label}_phase*.csv"))
    if not files:
        raise SystemExit(
            f"no {label}_phase*.csv files found under {root}"
        )

    groups = {
        "A": [p for p in files if "_phaseA_" in p.name],
        "B": [p for p in files if "_phaseB_" in p.name],
        "C": [p for p in files if "_phaseC_" in p.name],
    }

    outputs = [
        (
            root / f"{label}_combined_all.csv",
            files,
        ),
        (
            root / f"{label}_combined_phaseA_primary.csv",
            groups["A"],
        ),
        (
            root / f"{label}_combined_phaseB_gainmap.csv",
            groups["B"],
        ),
        (
            root / f"{label}_combined_phaseC_stage.csv",
            groups["C"],
        ),
    ]

    for path, inputs in outputs:
        if not inputs:
            continue
        count = write_combined(path, inputs)
        print(f"{path}: {count} rows from {len(inputs)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
