"""Axis conversion is separate from measured values; no smoothing is applied."""
import numpy as np


def x_coordinates(rows, mode):
    if mode == 0:
        return np.array([row.get("sample_index", i) for i, row in enumerate(rows)], dtype=float), "samples"
    if mode == 2 and all(row.get("device_ms") is not None for row in rows):
        return np.array([row["device_ms"] / 1000 for row in rows], dtype=float), "device_s"
    return np.array([row["t"] for row in rows], dtype=float), "host_s"
