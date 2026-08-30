import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from .LIB.a6_motion_controller import motion_controller
from .LIB.config import LEVELING_CONFIG, RIG_CONFIG
from .LIB.logging_config import get_logger

logger = get_logger("leveling")


MAX_AXIS = len(RIG_CONFIG.axes)
levelingOffset = [0.0] * MAX_AXIS  # mm
levelingActive = False
levelingFixedAxis: int | None = None
LEVELING_LOAD_RATE_UNITS_PER_PERCENT = LEVELING_CONFIG.load_rate_units_per_percent
LEVELING_LOAD_BIAS_BY_AXIS = LEVELING_CONFIG.load_bias_by_axis
LEVELING_LOAD_RATE_SAMPLES = LEVELING_CONFIG.load_rate_samples
LEVELING_LOAD_RATE_SAMPLE_DELAY = LEVELING_CONFIG.load_rate_sample_delay_s
LEVELING_SETTLE_TIME = LEVELING_CONFIG.settle_time_s
LEVELING_SETTLE_CHECKS = LEVELING_CONFIG.settle_checks
LEVELING_SETTLE_CHECK_DELAY = LEVELING_CONFIG.settle_check_delay_s
LEVELING_SETTLE_LOAD_DELTA = LEVELING_CONFIG.settle_load_delta
LEVELING_VERIFY_BATCHES = LEVELING_CONFIG.verify_batches
LEVELING_VERIFY_CORRECTIONS = LEVELING_CONFIG.verify_corrections
LEVELING_MIN_TOLERANCE = LEVELING_CONFIG.minimum_tolerance
LEVELING_LOG_FILE = Path(__file__).with_name("LOG") / "leveling.log"

_leveling_log_lock = threading.Lock()
_leveling_stop_event = threading.Event()

zeroOffset: list[float] = []
prevPos: list[float] = []


def _state_not_initialized(*_args):
    raise RuntimeError("Leveling state is not initialized")


enabled_axes: Callable[[int, int | None], list[int]] = _state_not_initialized
axis_enabled: Callable[[int], bool] = _state_not_initialized


def _log(message):
    logger.info(message)
    try:
        with _leveling_log_lock:
            LEVELING_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LEVELING_LOG_FILE.open("a", encoding="utf-8") as logFile:
                logFile.write(f"{message}\n")
    except OSError:
        pass


class LevelingStopped(Exception):
    pass


def stop_leveling():
    if not _leveling_stop_event.is_set():
        _leveling_stop_event.set()
        if levelingActive:
            _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Stop requested.")


def _check_leveling_stop():
    if _leveling_stop_event.is_set():
        raise LevelingStopped()


def _wait_for_leveling_delay(seconds):
    if _leveling_stop_event.wait(seconds):
        raise LevelingStopped()


class _CommandsState(Protocol):
    zeroOffset: list[float]
    prevPos: list[float]
    enabled_axes: Callable[[int, int | None], list[int]]
    axis_enabled: Callable[[int], bool]


def _sync_command_state():
    global zeroOffset
    global prevPos
    global enabled_axes
    global axis_enabled

    from . import SimHubCommands as commands

    commands_state = cast(_CommandsState, commands)
    zeroOffset = commands_state.zeroOffset
    prevPos = commands_state.prevPos
    enabled_axes = commands_state.enabled_axes
    axis_enabled = commands_state.axis_enabled


def print_leveling_offset_delta(previousOffsets):
    if previousOffsets is None:
        return

    deltaText = ", ".join(
        f"{axis}: {levelingOffset[axis - 1] - previousOffsets[axis - 1]:+.2f}mm"
        for axis in LEVELING_OFFSET_AXES
    )
    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Delta [{deltaText}]")

def leveling():
    global levelingActive
    global levelingFixedAxis

    if levelingActive:
        return

    levelingActive = True
    levelingFixedAxis = None
    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Started.")
    try:
        qualityPassed = leveling4()
    except LevelingStopped:
        offsetsSaved = save_leveling_offsets()
        offsetText = ", ".join(
            f"{axis}: {levelingOffset[axis - 1]:.2f}mm"
            for axis in LEVELING_OFFSET_AXES
        )
        if offsetsSaved:
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
                f"Stopped; current offsets saved [{offsetText}]."
            )
        else:
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
                f"Stopped; current offsets could not be saved [{offsetText}]."
            )
    except Exception as error:
        _log(
            f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
            f"Failed: {error}"
        )
        raise
    else:
        result = "Completed." if qualityPassed else "Completed with warning."
        _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] {result}")
    finally:
        levelingActive = False
        levelingFixedAxis = None
        _leveling_stop_event.clear()

def leveling2():
    _sync_command_state()
    offsetsLoaded = load_leveling_offsets()
    previousOffsets = levelingOffset.copy() if offsetsLoaded else None
    filteredCurrents = None
    if offsetsLoaded:
        apply_leveling_offsets()

    stages = LEVELING_CONFIG.stages[1:] if offsetsLoaded else LEVELING_CONFIG.stages
    for stage in stages:
        filteredCurrents = level2(
            stage.step_mm,
            stage.max_iterations,
            stage.target_tolerance,
            stage.kp,
            stage.allow_lowering,
            stage.filter_alpha,
            stage.lower_step_factor,
            filteredCurrents,
            rawWeight=stage.raw_weight,
            integralGain=stage.integral_gain,
        )
        save_leveling_offsets()
    print_leveling_offset_delta(previousOffsets)

def leveling3():
    _sync_command_state()
    offsetsLoaded = load_leveling_offsets()
    previousOffsets = levelingOffset.copy() if offsetsLoaded else None
    if offsetsLoaded:
        apply_leveling_offsets()
        filteredCurrents = None
    else:
        filteredCurrents = level3(0.2, 35, 2, 0.0022, False, 0.25, 0.25, rawWeight=0.30, coupling=0.25)
        save_leveling_offsets()

    filteredCurrents = level3(0.1, 30, 1, 0.0012, True, 0.25, 0.25, filteredCurrents, rawWeight=0.55, coupling=0.20)
    save_leveling_offsets()
    level3(0.05, 20, 0.5, 0.0005, True, 0.30, 0.20, filteredCurrents, rawWeight=0.75, coupling=0.25)
    save_leveling_offsets()
    print_leveling_offset_delta(previousOffsets)


def leveling4():
    global levelingFixedAxis

    _sync_command_state()
    _check_leveling_stop()
    offsetsLoaded = load_leveling_offsets()
    previousOffsets = levelingOffset.copy()
    if offsetsLoaded:
        apply_leveling_offsets()

    currentAxes = enabled_axes(LEVELING_OFFSET_AXES[0], LEVELING_OFFSET_AXES[-1])
    if not currentAxes:
        return

    startLoadRates = _read_level_load_rates(currentAxes)
    fixedAxis, fixedLoad = max(
        zip(currentAxes, startLoadRates, strict=False),
        key=lambda axisCurrent: axisCurrent[1],
    )
    levelingFixedAxis = fixedAxis
    _log(
        f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
        f"Axis {fixedAxis} remains fixed "
        f"(highest start load: {fixedLoad / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%)."
    )

    (
        filteredCurrents,
        acceptedScore,
        acceptedPassed,
        acceptedStable,
    ) = _verify_leveling_result(
        currentAxes, LEVELING_CONFIG.stages[0].target_tolerance, fixedAxis
    )
    acceptedOffsets = levelingOffset.copy()
    if acceptedStable and acceptedPassed and not offsetsLoaded:
        save_leveling_offsets()

    def evaluateStage(stageName, targetTolerance):
        nonlocal acceptedOffsets
        nonlocal acceptedPassed
        nonlocal acceptedScore
        nonlocal acceptedStable

        _check_leveling_stop()
        currents, score, passed, stable = _verify_leveling_result(
            currentAxes,
            targetTolerance,
            fixedAxis,
        )
        improved = score < acceptedScore or not acceptedStable
        if stable and improved:
            previousScore = acceptedScore
            acceptedOffsets = levelingOffset.copy()
            acceptedScore = score
            acceptedPassed = passed
            acceptedStable = stable
            save_leveling_offsets()
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
                f"{stageName} accepted "
                f"(score {previousScore / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp "
                f"-> {score / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp)."
            )
            return currents

        reason = "unstable" if not stable else "not improved"
        _log(
            f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
            f"{stageName} rejected ({reason}, "
            f"score={score / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"accepted={acceptedScore / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp)."
        )
        _check_leveling_stop()
        if levelingOffset != acceptedOffsets:
            levelingOffset[:] = acceptedOffsets
            apply_leveling_offsets()
            _wait_for_leveling_settle(currentAxes)
        return None

    for index, stage in enumerate(LEVELING_CONFIG.stages):
        level2(
            stage.step_mm,
            stage.max_iterations,
            stage.target_tolerance,
            stage.kp,
            stage.allow_lowering,
            stage.filter_alpha,
            stage.lower_step_factor,
            None if index == 0 else filteredCurrents,
            rawWeight=stage.raw_weight,
            integralGain=stage.integral_gain,
            fixedAxis=fixedAxis,
        )
        filteredCurrents = evaluateStage(
            f"{stage.name.title()} stage", stage.target_tolerance
        )
    print_leveling_offset_delta(previousOffsets)
    return acceptedStable and acceptedPassed

def _read_level_load_rates(
    currentAxes,
    samples=LEVELING_LOAD_RATE_SAMPLES,
    sampleDelay=LEVELING_LOAD_RATE_SAMPLE_DELAY,
):
    _check_leveling_stop()
    if samples <= 1:
        return [motion_controller.read_load_rate(axis) for axis in currentAxes]

    readingsByAxis = [[] for _ in currentAxes]
    for sample in range(samples):
        _check_leveling_stop()
        for index, axis in enumerate(currentAxes):
            _check_leveling_stop()
            readingsByAxis[index].append(motion_controller.read_load_rate(axis))
        if sample < samples - 1:
            _wait_for_leveling_delay(sampleDelay)

    currents = []
    for readings in readingsByAxis:
        sortedReadings = sorted(readings)
        middle = len(sortedReadings) // 2
        if len(sortedReadings) % 2:
            median = sortedReadings[middle]
        else:
            median = (sortedReadings[middle - 1] + sortedReadings[middle]) / 2.0
        currents.append(int(round(median)))
    return currents


def _wait_for_leveling_settle(currentAxes):
    _wait_for_leveling_delay(LEVELING_SETTLE_TIME)
    previousCurrents = _read_level_load_rates(currentAxes)

    for _ in range(LEVELING_SETTLE_CHECKS):
        _wait_for_leveling_delay(LEVELING_SETTLE_CHECK_DELAY)
        currents = _read_level_load_rates(currentAxes)
        loadDeltas = [
            abs(current - previous)
            for current, previous in zip(currents, previousCurrents, strict=False)
        ]
        maxDelta = max(loadDeltas)
        if maxDelta <= LEVELING_SETTLE_LOAD_DELTA:
            return True
        previousCurrents = currents

    deltaText = ", ".join(
        f"{axis}: {delta / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
        for axis, delta in zip(currentAxes, loadDeltas, strict=False)
    )
    _log(
        f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] "
        f"Loads did not settle (deltas [{deltaText}], "
        f"max={maxDelta / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp)."
    )
    return False


def _leveling_target_load_rate(values, currentAxes, fixedAxis=None):
    if fixedAxis in currentAxes:
        return values[currentAxes.index(fixedAxis)]
    return sum(values) / len(values)


def _leveling_tolerance(_values, _currentAxes, targetTolerance, _fixedAxis=None):
    return max(
        targetTolerance * LEVELING_LOAD_RATE_UNITS_PER_PERCENT,
        LEVELING_MIN_TOLERANCE,
    )


def _verify_leveling_result(currentAxes, targetTolerance, fixedAxis=None):
    """Measure the final position independently and report its actual quality."""
    batches = [
        _read_level_load_rates(currentAxes)
        for _ in range(LEVELING_VERIFY_BATCHES)
    ]
    verifiedCurrents = []
    batchRanges = []
    for axisIndex in range(len(currentAxes)):
        axisReadings = sorted(batch[axisIndex] for batch in batches)
        batchRanges.append(axisReadings[-1] - axisReadings[0])
        verifiedCurrents.append(float(axisReadings[len(axisReadings) // 2]))
    deviations = _leveling_load_deviations(
        verifiedCurrents,
        currentAxes,
        fixedAxis,
    )
    averageCurrent = sum(verifiedCurrents) / len(verifiedCurrents)
    targetLoadRate = _leveling_target_load_rate(
        verifiedCurrents,
        currentAxes,
        fixedAxis,
    )
    tolerance = _leveling_tolerance(
        verifiedCurrents,
        currentAxes,
        targetTolerance,
        fixedAxis,
    )
    spread = max(verifiedCurrents) - min(verifiedCurrents)
    maxBatchRange = max(batchRanges)
    stabilityLimit = max(tolerance * 2.0, 20.0)
    stable = maxBatchRange <= stabilityLimit
    passed = stable and all(
        abs(deviation) <= tolerance for deviation in deviations
    )
    currentText = ", ".join(
        f"{axis}: {current / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%"
        for axis, current in zip(currentAxes, verifiedCurrents, strict=False)
    )
    rangeText = ", ".join(
        f"{axis}: {batchRange / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
        for axis, batchRange in zip(currentAxes, batchRanges, strict=False)
    )
    result = "OK" if passed else ("WARNING" if stable else "UNSTABLE")
    _log(
        f"{datetime.now().strftime('%H:%M:%S')} [LEVEL VERIFY] {result}, "
        f"load [{currentText}], "
        f"avg={averageCurrent / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
        f"target={targetLoadRate / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
        f"tol={tolerance / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
        f"spread={spread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
        f"batch-ranges [{rangeText}], "
        f"max-range={maxBatchRange / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
    )
    score = max(abs(deviation) for deviation in deviations)
    return verifiedCurrents, score, passed, stable


def _leveling_load_deviations(values, currentAxes, fixedAxis=None):
    targetLoadRate = _leveling_target_load_rate(values, currentAxes, fixedAxis)
    return [
        0.0
        if axis == fixedAxis
        else value - targetLoadRate - LEVELING_LOAD_BIAS_BY_AXIS.get(axis, 0.0)
        for axis, value in zip(currentAxes, values, strict=False)
    ]

def _leveling_score(filteredDeviations, rawDeviations, rawWeight):
    filteredScore = max(abs(deviation) for deviation in filteredDeviations)
    rawScore = max(abs(deviation) for deviation in rawDeviations)
    return (filteredScore * (1.0 - rawWeight)) + (rawScore * rawWeight)

def level2(
    stepMM,
    maxIterations,
    targetTolerance,
    kp,
    allowLowering,
    filterAlpha,
    lowerStepFactor,
    initialFilteredCurrents=None,
    rawWeight=0.35,
    integralGain=0.0,
    fixedAxis=None,
):
    _sync_command_state()
    global levelingOffset
    maxOffset = 6.0 # mm maximum offset allowed for leveling
    minImprovement = max(1.0, stepMM * 20.0)
    maxNoImprovement = 7
    currentAxes = enabled_axes(4, 7)
    if not currentAxes:
        return initialFilteredCurrents
    if initialFilteredCurrents is None or len(initialFilteredCurrents) != len(currentAxes):
        filteredCurrents = None
    else:
        filteredCurrents = initialFilteredCurrents.copy()
    bestOffsets = levelingOffset.copy()
    bestSpread = None
    improvementSpread = None
    bestIteration = 0
    noImprovement = 0
    integralDeviations = [0.0] * len(currentAxes)
    integralDecay = 0.85
    integralLimit = 80.0

    for iteration in range(1, maxIterations + 1):
        _check_leveling_stop()
        currents = _read_level_load_rates(currentAxes)
        if filteredCurrents is None:
            filteredCurrents = [float(current) for current in currents]
        else:
            filteredCurrents = [
                (filterAlpha * current) + ((1.0 - filterAlpha) * filtered)
                for current, filtered in zip(currents, filteredCurrents, strict=False)
            ]

        averageCurrent = sum(filteredCurrents) / len(filteredCurrents)
        targetLoadRate = _leveling_target_load_rate(
            filteredCurrents,
            currentAxes,
            fixedAxis,
        )
        tolerance = _leveling_tolerance(
            filteredCurrents,
            currentAxes,
            targetTolerance,
            fixedAxis,
        )
        rawTolerance = max(tolerance * 2.0, 20.0)
        currentSpread = max(filteredCurrents) - min(filteredCurrents)
        rawSpread = max(currents) - min(currents)
        deviations = _leveling_load_deviations(
            filteredCurrents,
            currentAxes,
            fixedAxis,
        )
        rawDeviations = _leveling_load_deviations(
            currents,
            currentAxes,
            fixedAxis,
        )
        scoreSpread = _leveling_score(deviations, rawDeviations, rawWeight)
        if bestSpread is None or scoreSpread < bestSpread:
            bestSpread = scoreSpread
            bestOffsets = levelingOffset.copy()
            bestIteration = iteration

        if improvementSpread is None or scoreSpread < improvementSpread - minImprovement:
            improvementSpread = scoreSpread
            noImprovement = 0
        else:
            noImprovement += 1

        currentText = ", ".join(
            f"{axis}: {current / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%"
            for axis, current in zip(currentAxes, currents, strict=False)
        )
        offsetText = ", ".join(
            f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
        )
        _log(
            f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
            f"{iteration}/{maxIterations}, load [{currentText}], "
            f"offs [{offsetText}], "
            f"avg={averageCurrent / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
            f"target={targetLoadRate / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
            f"tol={tolerance / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"spread={currentSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"raw={rawSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"score={scoreSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
        )

        if all(abs(deviation) <= tolerance for deviation in deviations) and all(
            abs(deviation) <= rawTolerance for deviation in rawDeviations
        ):
            break

        if noImprovement >= maxNoImprovement:
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
                f"No improvement for {noImprovement} iterations; "
                f"using best result from iteration {bestIteration}."
            )
            break

        correctionDeviations = [
            ((1.0 - rawWeight) * filteredDeviation) + (rawWeight * rawDeviation)
            for filteredDeviation, rawDeviation in zip(deviations, rawDeviations, strict=False)
        ]
        if integralGain:
            integralDeviations = [
                max(
                    -integralLimit,
                    min(integralLimit, (integralDecay * integral) + deviation)
                )
                for integral, deviation in zip(integralDeviations, correctionDeviations, strict=False)
            ]
            correctionDeviations = [
                deviation + (integralGain * integral)
                for deviation, integral in zip(correctionDeviations, integralDeviations, strict=False)
            ]
        severeUnderload = any(
            deviation < -max(tolerance * 2.0, 15.0)
            for deviation in correctionDeviations
        )
        loweringStepFactor = lowerStepFactor * (0.5 if severeUnderload else 1.0)

        deltasByAxis = {}
        for axis, deviation in zip(currentAxes, correctionDeviations, strict=False):
            if axis == fixedAxis:
                continue
            if abs(deviation) <= tolerance:
                continue

            if deviation < 0:
                deltaMM = min(-kp * deviation, stepMM)
            elif allowLowering:
                deltaMM = max(-kp * deviation, -stepMM * loweringStepFactor)
            else:
                continue

            if abs(deltaMM) >= 0.001:
                deltasByAxis[axis] = deltaMM

        if allowLowering and deltasByAxis and fixedAxis is None:
            averageDelta = sum(deltasByAxis.values()) / len(currentAxes)
            deltasByAxis = {
                axis: deltaMM - averageDelta
                for axis, deltaMM in deltasByAxis.items()
            }

        moved = False
        for axis, deltaMM in deltasByAxis.items():
            _check_leveling_stop()
            if abs(deltaMM) < 0.001:
                continue

            levelingOffset[axis - 1] += deltaMM
            levelingOffset[axis - 1] = max(
                -maxOffset,
                min(levelingOffset[axis - 1], maxOffset)
            )
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
            moved = True

        if not moved:
            break

        _wait_for_leveling_settle(currentAxes)

    if bestSpread is not None and bestOffsets != levelingOffset:
        for axis in currentAxes:
            _check_leveling_stop()
            if axis == fixedAxis:
                continue
            if bestOffsets[axis - 1] == levelingOffset[axis - 1]:
                continue

            levelingOffset[axis - 1] = bestOffsets[axis - 1]
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
        _wait_for_leveling_settle(currentAxes)

    # Do not carry filter values from a discarded trial position into the next
    # leveling stage. The independent measurement also validates the offsets
    # that are actually left applied to the rig.
    (
        filteredCurrents,
        verifiedScore,
        verified,
        verificationStable,
    ) = _verify_leveling_result(currentAxes, targetTolerance, fixedAxis)

    # A warning must have an effect: make a few small corrections based on
    # independent measurements. Keep only corrections that also improve the
    # following verification, otherwise restore the last verified position.
    for _ in range(LEVELING_VERIFY_CORRECTIONS):
        if verified or not verificationStable:
            break

        verifiedTolerance = _leveling_tolerance(
            filteredCurrents,
            currentAxes,
            targetTolerance,
            fixedAxis,
        )
        verifiedDeviations = _leveling_load_deviations(
            filteredCurrents,
            currentAxes,
            fixedAxis,
        )
        correctionOffsets = levelingOffset.copy()
        correctionMade = False

        for axis, deviation in zip(currentAxes, verifiedDeviations, strict=False):
            _check_leveling_stop()
            if axis == fixedAxis:
                continue
            if abs(deviation) <= verifiedTolerance:
                continue
            if deviation < 0:
                deltaMM = min(-kp * deviation, stepMM)
            elif allowLowering:
                deltaMM = max(-kp * deviation, -stepMM * lowerStepFactor)
            else:
                continue
            if abs(deltaMM) < 0.001:
                continue

            levelingOffset[axis - 1] = max(
                -maxOffset,
                min(levelingOffset[axis - 1] + deltaMM, maxOffset)
            )
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(
                axis, targetPosition, log_hex=False, check_crc=True
            )
            correctionMade = True

        if not correctionMade:
            break

        _wait_for_leveling_settle(currentAxes)
        (
            correctedCurrents,
            correctedScore,
            corrected,
            correctedStable,
        ) = _verify_leveling_result(currentAxes, targetTolerance, fixedAxis)
        if correctedStable and correctedScore < verifiedScore:
            filteredCurrents = correctedCurrents
            verifiedScore = correctedScore
            verified = corrected
            verificationStable = correctedStable
            continue

        for axis in currentAxes:
            _check_leveling_stop()
            if axis == fixedAxis:
                continue
            if levelingOffset[axis - 1] == correctionOffsets[axis - 1]:
                continue
            levelingOffset[axis - 1] = correctionOffsets[axis - 1]
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(
                axis, targetPosition, log_hex=False, check_crc=True
            )
        _wait_for_leveling_settle(currentAxes)
        break

    offsetText = ", ".join(
        f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
    )

    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Offsets [{offsetText}]")
    return filteredCurrents

def _apply_diagonal_coupling(deviations, currents, currentAxes, coupling, minContactLoad=80.0):
    coupledDeviations = list(deviations)
    axisIndex = {axis: index for index, axis in enumerate(currentAxes)}

    for axisA, axisB in ((4, 6), (5, 7)):
        if axisA not in axisIndex or axisB not in axisIndex:
            continue

        indexA = axisIndex[axisA]
        indexB = axisIndex[axisB]
        if currents[indexA] < minContactLoad or currents[indexB] < minContactLoad:
            continue

        deviationA = deviations[indexA]
        deviationB = deviations[indexB]
        candidateA = deviationA - (coupling * deviationB)
        candidateB = deviationB - (coupling * deviationA)

        if deviationA * candidateA > 0:
            coupledDeviations[indexA] = candidateA
        else:
            coupledDeviations[indexA] = deviationA

        if deviationB * candidateB > 0:
            coupledDeviations[indexB] = candidateB
        else:
            coupledDeviations[indexB] = deviationB

    return coupledDeviations

def level3(stepMM, maxIterations, targetTolerance, kp, allowLowering, filterAlpha, lowerStepFactor, initialFilteredCurrents=None, rawWeight=0.35, coupling=0.35):
    _sync_command_state()
    global levelingOffset
    settleTime = 1.0 # seconds to wait after moving before checking position again
    maxOffset = 6.0 # mm maximum offset allowed for leveling
    minImprovement = max(1.0, stepMM * 20.0)
    maxNoImprovement = 7
    currentAxes = enabled_axes(4, 7)
    if not currentAxes:
        return initialFilteredCurrents
    if initialFilteredCurrents is None or len(initialFilteredCurrents) != len(currentAxes):
        filteredCurrents = None
    else:
        filteredCurrents = initialFilteredCurrents.copy()
    bestOffsets = levelingOffset.copy()
    bestSpread = None
    improvementSpread = None
    bestIteration = 0
    noImprovement = 0

    for iteration in range(1, maxIterations + 1):
        _check_leveling_stop()
        currents = _read_level_load_rates(currentAxes)
        if filteredCurrents is None:
            filteredCurrents = [float(current) for current in currents]
        else:
            filteredCurrents = [
                (filterAlpha * current) + ((1.0 - filterAlpha) * filtered)
                for current, filtered in zip(currents, filteredCurrents, strict=False)
            ]

        averageCurrent = sum(filteredCurrents) / len(filteredCurrents)
        tolerance = _leveling_tolerance(
            filteredCurrents,
            currentAxes,
            targetTolerance,
        )
        rawTolerance = max(tolerance * 2.0, 20.0)
        currentSpread = max(filteredCurrents) - min(filteredCurrents)
        rawSpread = max(currents) - min(currents)
        deviations = _leveling_load_deviations(filteredCurrents, currentAxes)
        rawDeviations = _leveling_load_deviations(currents, currentAxes)
        scoreSpread = _leveling_score(deviations, rawDeviations, rawWeight)
        if bestSpread is None or scoreSpread < bestSpread:
            bestSpread = scoreSpread
            bestOffsets = levelingOffset.copy()
            bestIteration = iteration

        if improvementSpread is None or scoreSpread < improvementSpread - minImprovement:
            improvementSpread = scoreSpread
            noImprovement = 0
        else:
            noImprovement += 1

        currentText = ", ".join(
            f"{axis}: {current / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%"
            for axis, current in zip(currentAxes, currents, strict=False)
        )
        offsetText = ", ".join(
            f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
        )
        _log(
            f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
            f"{iteration}/{maxIterations}, load [{currentText}], "
            f"offs [{offsetText}], "
            f"avg={averageCurrent / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
            f"tol={tolerance / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"spread={currentSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"raw={rawSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"score={scoreSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
        )

        if all(abs(deviation) <= tolerance for deviation in deviations) and all(
            abs(deviation) <= rawTolerance for deviation in rawDeviations
        ):
            break

        if noImprovement >= maxNoImprovement:
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
                f"No improvement for {noImprovement} iterations; "
                f"using best result from iteration {bestIteration}."
            )
            break

        correctionDeviations = [
            ((1.0 - rawWeight) * filteredDeviation) + (rawWeight * rawDeviation)
            for filteredDeviation, rawDeviation in zip(deviations, rawDeviations, strict=False)
        ]
        correctionDeviations = _apply_diagonal_coupling(
            correctionDeviations,
            currents,
            currentAxes,
            coupling,
        )
        severeUnderload = any(
            deviation < -max(tolerance * 2.0, 15.0)
            for deviation in correctionDeviations
        )
        loweringStepFactor = lowerStepFactor * (0.5 if severeUnderload else 1.0)

        deltasByAxis = {}
        for axis, deviation in zip(currentAxes, correctionDeviations, strict=False):
            if abs(deviation) <= tolerance:
                continue

            if deviation < 0:
                deltaMM = min(-kp * deviation, stepMM)
            elif allowLowering:
                deltaMM = max(-kp * deviation, -stepMM * loweringStepFactor)
            else:
                continue

            if abs(deltaMM) >= 0.001:
                deltasByAxis[axis] = deltaMM

        if allowLowering and deltasByAxis:
            averageDelta = sum(deltasByAxis.values()) / len(currentAxes)
            deltasByAxis = {
                axis: deltaMM - averageDelta
                for axis, deltaMM in deltasByAxis.items()
            }

        moved = False
        for axis, deltaMM in deltasByAxis.items():
            _check_leveling_stop()
            if abs(deltaMM) < 0.001:
                continue

            levelingOffset[axis - 1] += deltaMM
            levelingOffset[axis - 1] = max(
                -maxOffset,
                min(levelingOffset[axis - 1], maxOffset)
            )
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
            moved = True

        if not moved:
            break

        _wait_for_leveling_delay(settleTime)

    if bestSpread is not None and bestOffsets != levelingOffset:
        for axis in currentAxes:
            _check_leveling_stop()
            if bestOffsets[axis - 1] == levelingOffset[axis - 1]:
                continue

            levelingOffset[axis - 1] = bestOffsets[axis - 1]
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
        _wait_for_leveling_delay(settleTime)

    offsetText = ", ".join(
        f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
    )

    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Offsets [{offsetText}]")
    return filteredCurrents

LEVELING_OFFSET_AXES = list(LEVELING_CONFIG.offset_axes)
LEVELING_OFFSET_FILE = Path(__file__).with_name("INI") / "leveling_offsets.json"

def load_leveling_offsets():
    global levelingOffset
    if not LEVELING_OFFSET_FILE.exists():
        return False

    try:
        data = json.loads(LEVELING_OFFSET_FILE.read_text(encoding="utf-8"))
        offsets = data.get("offsets", data)
        loadedOffsets = [float(value) for value in offsets]
    except Exception as e:
        _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Could not load offsets: {e}")
        return False

    for axis in LEVELING_OFFSET_AXES:
        if axis - 1 < len(loadedOffsets):
            levelingOffset[axis - 1] = loadedOffsets[axis - 1]

    offsetText = ", ".join(
        f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in LEVELING_OFFSET_AXES
    )
    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Loaded offsets [{offsetText}]")
    return True

def save_leveling_offsets():
    data = {
        "offsets": [round(value, 4) for value in levelingOffset],
        "axes": LEVELING_OFFSET_AXES,
        "savedAt": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        LEVELING_OFFSET_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Could not save offsets: {e}")
        return False
    return True

def apply_leveling_offsets():
    _sync_command_state()
    for axis in LEVELING_OFFSET_AXES:
        if not axis_enabled(axis):
            continue
        targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
        prevPos[axis - 1] = targetPosition
        motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
    if levelingActive:
        _wait_for_leveling_delay(1.0)
    else:
        time.sleep(1.0)

def legacy_leveling():
    level (0.2, 30, 2, True, False)
    level (0.1, 20, 1, False, True)
    level (0.05, 10, 0.5, False, True)

def level (stepMM, maxIterations,  targetTolerance, limitAxis, allowLowering):
    _sync_command_state()
    global levelingOffset
    settleTime = 1.0 # seconds to wait after moving before checking position again
    maxOffset = 6.0 # mm maximum offset allowed for leveling
    minImprovement = max(1.0, stepMM * 20.0)
    maxNoImprovement = 4 if not limitAxis else 8
    currentAxes = enabled_axes(4, 7)
    if not currentAxes:
        return levelingOffset
    levelingAxes = []
    bestOffsets = levelingOffset.copy()
    bestSpread = None
    improvementSpread = None
    bestIteration = 0
    noImprovement = 0

    for iteration in range(1, maxIterations + 1):
        _check_leveling_stop()
        currents = [motion_controller.read_load_rate(axis) for axis in currentAxes]
        averageCurrent = sum(currents) / len(currents)
        tolerance = _leveling_tolerance(
            currents,
            currentAxes,
            targetTolerance,
        )
        currentsByAxis = dict(zip(currentAxes, currents, strict=False))
        currentSpread = max(currents) - min(currents)
        if bestSpread is None or currentSpread < bestSpread:
            bestSpread = currentSpread
            bestOffsets = levelingOffset.copy()
            bestIteration = iteration

        if improvementSpread is None or currentSpread < improvementSpread - minImprovement:
            improvementSpread = currentSpread
            noImprovement = 0
        else:
            noImprovement += 1

        if limitAxis:
            if iteration == 1: # first run only with 2 axis with the lowest current
                belowAverageAxes = [
                    axis for axis, current in sorted(
                        currentsByAxis.items(),
                        key=lambda item: item[1]
                    )
                    if current < averageCurrent
                ]
                if len(belowAverageAxes) < 2:
                    break
                levelingAxes = belowAverageAxes[:2]
        else:
            levelingAxes = currentAxes

        if not levelingAxes:
            break

        currentText = ", ".join(
            f"{axis}: {current / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%"
            for axis, current in zip(currentAxes, currents, strict=False)
        )
        offsetText = ", ".join(
            f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
        )
        _log(
            f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
            f"{iteration}/{maxIterations}, load [{currentText}], "
            f"offs [{offsetText}], "
            f"avg={averageCurrent / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}%, "
            f"tol={tolerance / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp, "
            f"spread={currentSpread / LEVELING_LOAD_RATE_UNITS_PER_PERCENT:.1f}pp"
        )

        deviations = [currentsByAxis[axis] - averageCurrent for axis in levelingAxes]
        if all(abs(deviation) <= tolerance for deviation in deviations):
            break

        if noImprovement >= maxNoImprovement:
            _log(
                f"{datetime.now().strftime('%H:%M:%S')} [LEVEL] "
                f"No improvement for {noImprovement} iterations; "
                f"using best result from iteration {bestIteration}."
            )
            break

        for axis in levelingAxes:
            _check_leveling_stop()
            deviation = currentsByAxis[axis] - averageCurrent
            if abs(deviation) <= tolerance:
                continue

            if deviation < 0:
                levelingOffset[axis - 1] += stepMM
            elif allowLowering:
                levelingOffset[axis - 1] -= stepMM * 0.5
            else:
                continue

            levelingOffset[axis - 1] = max(
                -maxOffset,
                min(levelingOffset[axis - 1], maxOffset)
            )
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)

        _wait_for_leveling_delay(settleTime)

    if bestSpread is not None and bestOffsets != levelingOffset:
        for axis in currentAxes:
            _check_leveling_stop()
            if bestOffsets[axis - 1] == levelingOffset[axis - 1]:
                continue

            levelingOffset[axis - 1] = bestOffsets[axis - 1]
            targetPosition = zeroOffset[axis - 1] + levelingOffset[axis - 1]
            prevPos[axis - 1] = targetPosition
            motion_controller.planner_set_position_mm(axis, targetPosition, log_hex=False, check_crc=True)
        _wait_for_leveling_delay(settleTime)

    offsetText = ", ".join(
        f"{axis}: {levelingOffset[axis - 1]:.2f}mm" for axis in currentAxes
    )

    _log(f"{datetime.now().strftime('%H:%M:%S')} [LEVELING] Offsets [{offsetText}]")
    return levelingOffset
