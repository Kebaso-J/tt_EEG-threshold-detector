# SPDX-License-Identifier: Apache-2.0
# Cocotb testbench for tt_um_eeg_threshold_detector
#
# Run with the standard Tiny Tapeout test flow (e.g. `make -B` inside the
# project's `test/` directory with SIM=icarus and the module set accordingly).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

CFG_TH_HIGH = 0
CFG_TH_LOW = 1
CFG_DEBOUNCE = 2
CFG_REFRACTORY = 3

# Values programmed into the configuration registers during this test.
TH_HIGH = 100
TH_LOW = 60
DEBOUNCE = 3
REFRACTORY = 5


async def settle(dut):
    """Let the DUT's registered (non-blocking) values settle before reading them.

    cocotb resumes in the same simulation time step as the clock edge, i.e. before
    the DUT's `<=` assignments have been applied. Reading uo_out (or any registered
    signal) immediately after `RisingEdge`/`ClockCycles` therefore returns the value
    from the cycle *before* the edge -- which makes assertions look one cycle late.
    Moving 1 ns past the edge guarantees the post-edge values are visible.
    """
    await Timer(1, unit="ns")


async def write_config(dut, addr: int, value: int):
    """Perform one configuration-write cycle: cfg_mode=1, cfg_addr=addr, ui_in=value."""
    dut.uio_in.value = (addr << 1) | 1
    dut.ui_in.value = value
    await RisingEdge(dut.clk)
    dut.uio_in.value = 0  # return to normal sample mode
    await settle(dut)     # so the written register can be read back


async def send_sample(dut, value: int):
    """Drive one sample value on ui_in during a normal (cfg_mode=0) cycle."""
    dut.ui_in.value = value
    await RisingEdge(dut.clk)
    await settle(dut)  # so uo_out reflects the FSM update for this sample


def read_internal_signal(handle, name: str):
    """Best-effort read for internal DUT signals that may be absent in gate-level netlists."""
    try:
        return getattr(handle, name).value
    except AttributeError:
        return "<unavailable>"


@cocotb.test()
async def test_threshold_detector(dut):
    """EEG-style digital threshold detector: hysteresis, debounce, and refractory behaviour."""

    dut._log.info("=== Starting test: tt_um_eeg_threshold_detector ===")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    # --- Reset ---
    dut._log.info("Applying reset")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    dut._log.info("Reset released")

    # --- Configure thresholds and timing ---
    dut._log.info("Configuring registers: th_high=100, th_low=60, debounce=3, refractory=5")

    await write_config(dut, CFG_TH_HIGH, TH_HIGH)
    dut._log.info(f"th_high after write = {read_internal_signal(dut.user_project, 'th_high')}")

    await write_config(dut, CFG_TH_LOW, TH_LOW)
    dut._log.info(f"th_low after write = {read_internal_signal(dut.user_project, 'th_low')}")

    await write_config(dut, CFG_DEBOUNCE, DEBOUNCE)
    dut._log.info(
        f"debounce_limit after write = {read_internal_signal(dut.user_project, 'debounce_limit')}"
    )

    await write_config(dut, CFG_REFRACTORY, REFRACTORY)
    dut._log.info(
        f"refractory_len after write = {read_internal_signal(dut.user_project, 'refractory_len')}"
    )

    # --- Baseline: low samples should never trigger detection ---
    dut._log.info("Stage 1: driving baseline low samples, expecting no detection")
    for _ in range(5):
        await send_sample(dut, 20)
    assert dut.uo_out.value[0] == 0, "Should not detect during baseline"
    assert dut.uo_out.value[1] == 1, "Should be armed/monitoring at baseline"
    dut._log.info("Stage 1 PASSED: no false detection at baseline, detector armed")

    # --- Drive samples above th_high for debounce_limit+1 cycles ---
    # The FSM counts DEBOUNCE consecutive above-th_high samples (above_count
    # reaching DEBOUNCE) and only then moves to DETECTED, so the state change
    # is visible after the (DEBOUNCE + 1)-th sample.
    dut._log.info("Stage 2: driving samples above th_high for debounce_limit+1 cycles")
    for _ in range(DEBOUNCE + 1):
        await send_sample(dut, 150)
        dut._log.info(
            f"sample=150 -> state={read_internal_signal(dut.user_project, 'state')}, "
            f"above_count={read_internal_signal(dut.user_project, 'above_count')}, "
            f"uo_out={dut.uo_out.value}"
        )
    assert dut.uo_out.value[0] == 1, "Should assert event_detected after debounce period"
    dut._log.info("Stage 2 PASSED: event_detected asserted after debounce period")

    # --- Detection should persist while signal stays above th_low (hysteresis) ---
    dut._log.info("Stage 3: sample dips below th_high but stays above th_low, checking hysteresis")
    await send_sample(dut, 80)  # below th_high, but still above th_low
    assert dut.uo_out.value[0] == 1, "Hysteresis should hold detection while above th_low"
    dut._log.info("Stage 3 PASSED: detection held during hysteresis band")

    # --- Dropping below th_low should move into refractory and clear the flag ---
    dut._log.info("Stage 4: sample drops below th_low, expecting transition to refractory")
    await send_sample(dut, 40)
    await ClockCycles(dut.clk, 1)
    await settle(dut)
    assert dut.uo_out.value[0] == 0, "Should clear detection once in refractory"
    assert dut.uo_out.value[3] == 1, "Should indicate refractory state"
    dut._log.info("Stage 4 PASSED: detection cleared, refractory state entered")

    # --- Wait out the refractory period ---
    # REFRACTORY holds the counter at REFRACTORY and counts it down to 0, so it
    # takes REFRACTORY + 1 cycles before the FSM is back in MONITOR.
    dut._log.info("Stage 5: waiting out refractory period, expecting re-arm")
    await ClockCycles(dut.clk, REFRACTORY + 1)
    await settle(dut)
    assert dut.uo_out.value[1] == 1, "Should be re-armed after refractory period"
    dut._log.info("Stage 5 PASSED: detector re-armed")

    # --- Confirm the detector can re-trigger after re-arming ---
    dut._log.info("Stage 6: driving high samples again, expecting re-detection")
    for _ in range(DEBOUNCE + 1):
        await send_sample(dut, 150)
    assert dut.uo_out.value[0] == 1, "Should re-detect after refractory period"
    dut._log.info("Stage 6 PASSED: detector re-triggered successfully")

    dut._log.info("=== All stages passed: tt_um_eeg_threshold_detector ===")