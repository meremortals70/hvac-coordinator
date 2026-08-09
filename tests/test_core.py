"""Tests for the pure modules. No Home Assistant required.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import UTC, datetime, time
from pathlib import Path

# The package __init__ imports Home Assistant, which is not installed here and
# is not needed: hci, models, modes and tariff are pure. Register a stand-in
# package pointing at the source directory so the relative imports inside those
# modules resolve without __init__.py ever being executed.
_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "hvac_coordinator"
_pkg = types.ModuleType("hvac_core")
_pkg.__path__ = [str(_SRC)]
sys.modules["hvac_core"] = _pkg

_hci = importlib.import_module("hvac_core.hci")
_models = importlib.import_module("hvac_core.models")
_modes = importlib.import_module("hvac_core.modes")
_tariff = importlib.import_module("hvac_core.tariff")

ComfortBand = _hci.ComfortBand
comfort_index = _hci.comfort_index
dry_bulb_for_index = _hci.dry_bulb_for_index
ActuatorStep = _models.ActuatorStep
Mode = _models.Mode
RoomConfig = _models.RoomConfig
RoomInputs = _models.RoomInputs
evaluate_room = _modes.evaluate_room
TariffSchedule = _tariff.TariffSchedule
TariffWindow = _tariff.TariffWindow

NOW = datetime(2026, 8, 8, 14, 30, tzinfo=UTC)

# Test fixtures only. No site data lives in source or in tests.
BANDS = {
    Mode.SLEEP: ComfortBand(24.0, 26.0),
    Mode.OCCUPIED: ComfortBand(25.0, 28.0),
    Mode.PRECOOL: ComfortBand(25.0, 28.0),
}

WINDOWS = (
    TariffWindow(time(0, 0), time(12, 0), "off_peak", coasting_permitted=False),
    TariffWindow(
        time(12, 0),
        time(0, 0),
        "peak",
        constraints=frozenset({"no_grid_import", "precool_opportunity"}),
    ),
)


def room(**overrides) -> RoomConfig:
    base = {
        "room_id": "office",
        "name": "Office",
        "climate_entity_id": "climate.office",
        "bands": BANDS,
    }
    base.update(overrides)
    return RoomConfig(**base)


class TestComfortIndex(unittest.TestCase):
    def test_humidity_raises_the_index_at_the_same_temperature(self):
        dry = comfort_index(24.0, 35.0)
        humid = comfort_index(24.0, 85.0)
        self.assertGreater(humid, dry)

    def test_index_is_monotonic_in_temperature(self):
        values = [comfort_index(t, 60.0) for t in range(16, 32)]
        self.assertEqual(values, sorted(values))

    def test_inverse_round_trips(self):
        for target in (18.0, 20.0, 23.5, 26.0):
            for rh in (30.0, 55.0, 80.0):
                dry_bulb = dry_bulb_for_index(target, rh)
                self.assertAlmostEqual(
                    comfort_index(dry_bulb, rh), target, places=2
                )

    def test_humid_night_gives_a_lower_setpoint_than_a_dry_one(self):
        """The whole reason the user never sets a setpoint."""
        humid = dry_bulb_for_index(19.0, 85.0)
        dry = dry_bulb_for_index(19.0, 40.0)
        self.assertLess(humid, dry)

    def test_band_rejects_inverted_bounds(self):
        with self.assertRaises(ValueError):
            ComfortBand(25.0, 22.0)


class TestModePrecedence(unittest.TestCase):
    def test_lockout_beats_everything(self):
        trace = evaluate_room(
            room(lockout_reason="upstairs renovation"),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=True,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.LOCKOUT)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIn("upstairs renovation", " ".join(trace.reasons))

    def test_precondition_beats_presence(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)

    def test_unknown_presence_holds_occupied(self):
        trace = evaluate_room(
            room(),
            RoomInputs(now=NOW, temperature_c=26.0, relative_humidity=60.0),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)

    def test_sleep_needs_presence_and_schedule(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=22.0,
                relative_humidity=60.0,
                presence=True,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(trace.mode, Mode.SLEEP)
        self.assertEqual(trace.band_low, 24.0)

    def test_coast_carries_the_displaced_band(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertIs(trace.base_mode, Mode.OCCUPIED)
        self.assertEqual(trace.band_low, 25.0)
        self.assertIs(trace.actuator, ActuatorStep.NONE)

    def test_cheap_window_does_not_coast_even_when_it_would_hold(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=True,
                coasting_permitted=False,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertTrue(any("coast" in r for r in trace.rejected))

    def test_precool_needs_both_the_window_and_demand_ahead(self):
        without = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
            ),
        )
        self.assertIsNot(without.mode, Mode.PRECOOL)

        with_demand = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
                forecast_demand_ahead=True,
            ),
        )
        self.assertIs(with_demand.mode, Mode.PRECOOL)

    def test_precool_targets_the_low_bound(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
                forecast_demand_ahead=True,
            ),
        )
        expected = dry_bulb_for_index(25.0, 55.0)
        self.assertAlmostEqual(trace.target_dry_bulb_c, expected, places=3)


class TestActuatorOrdering(unittest.TestCase):
    """Cheapest first: covers, fan, dry, compressor. Nothing skips a step."""

    def test_open_window_stops_everything(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=70.0,
                presence=True,
                opening_open=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)

    def test_covers_come_first_when_there_is_sun_to_block(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=70.0,
                presence=True,
                has_covers=True,
                illuminance_lux=20000.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)
        self.assertEqual(trace.demand, "cool")

    def test_covers_are_not_moved_in_the_dark(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                illuminance_lux=5.0,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("no solar gain" in r for r in trace.rejected))

    def test_fan_when_marginally_above_band(self):
        # Band high is 28.0; the fan margin is 0.5 HCI. 28 C at 35% reads 28.35.
        temp = 28.0
        rh = 35.0
        self.assertGreater(comfort_index(temp, rh), 28.0)
        self.assertLess(comfort_index(temp, rh), 28.5)
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW, temperature_c=temp, relative_humidity=rh, presence=True
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.FAN)

    def test_dry_mode_when_the_load_is_latent(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=80.0,
                presence=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.DRY)
        self.assertTrue(any("fan" in r for r in trace.rejected))

    def test_compressor_only_after_everything_else_is_ruled_out(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=33.0,
                relative_humidity=35.0,
                presence=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        rejected = " ".join(trace.rejected)
        self.assertIn("covers", rejected)
        self.assertIn("fan", rejected)
        self.assertIn("dry", rejected)

    def test_heating_never_reaches_for_fan_or_dry(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
            ),
        )
        self.assertEqual(trace.demand, "heat")
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("neither adds heat" in r for r in trace.rejected))

    def test_covers_admit_gain_when_the_room_is_too_cold(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                illuminance_lux=20000.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("admitting" in r for r in trace.reasons))

    def test_precool_stops_once_it_reaches_the_low_bound(self):
        """Precool drives to the low bound, then stops. It does not run on."""
        inputs = {
            "now": NOW,
            "relative_humidity": 50.0,
            "presence": True,
            "precool_opportunity": True,
            "forecast_demand_ahead": True,
        }
        # Below the low bound of 25.0 — nothing left to bank. 23 C at 50%
        # reads 23.6.
        cold = evaluate_room(room(), RoomInputs(temperature_c=23.0, **inputs))
        self.assertIs(cold.mode, Mode.PRECOOL)
        self.assertIsNone(cold.demand)
        self.assertIs(cold.actuator, ActuatorStep.NONE)

        # Inside the band but above the low bound — still banking. 26 C at
        # 50% reads 27.5, between the 25.0 low and the 28.0 high.
        warm = evaluate_room(room(), RoomInputs(temperature_c=26.0, **inputs))
        self.assertIs(warm.mode, Mode.PRECOOL)
        self.assertEqual(warm.demand, "cool")
        self.assertIsNot(warm.actuator, ActuatorStep.NONE)

    def test_no_actuation_without_a_reading(self):
        trace = evaluate_room(room(), RoomInputs(now=NOW, presence=True))
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIsNone(trace.hci)

    def test_trace_is_always_produced(self):
        trace = evaluate_room(room(), RoomInputs(now=NOW))
        self.assertEqual(trace.room_id, "office")
        self.assertIn("mode", trace.as_attributes())


class TestTariff(unittest.TestCase):
    def setUp(self):
        self.schedule = TariffSchedule(WINDOWS)

    def test_schedule_covers_the_whole_day(self):
        for hour in range(24):
            self.schedule.window_at(time(hour, 0))

    def test_constraints_are_carried_on_the_window(self):
        window = self.schedule.window_at(time(18, 0))
        self.assertIn("precool_opportunity", window.constraints)
        self.assertIn("no_grid_import", window.constraints)

    def test_coasting_can_be_forbidden_on_a_window(self):
        window = self.schedule.window_at(time(3, 0))
        self.assertFalse(window.coasting_permitted)

    def test_gap_in_schedule_is_rejected(self):
        with self.assertRaises(ValueError):
            TariffSchedule(
                (
                    TariffWindow(time(0, 0), time(6, 0), "cheap"),
                    TariffWindow(time(7, 0), time(0, 0), "standard"),
                )
            )

    def test_unrecognised_constraint_is_reported_not_dropped(self):
        schedule = TariffSchedule(
            (
                TariffWindow(
                    time(0, 0),
                    time(0, 0),
                    "standard",
                    constraints=frozenset({"export_curtailment"}),
                ),
            )
        )
        self.assertIn("export_curtailment", schedule.unrecognised_constraints())


if __name__ == "__main__":
    unittest.main()


class TestUnoccupiedAndHeadingHome(unittest.TestCase):
    """An unoccupied room is off. Heading home is the only thing that overrides it."""

    def test_unoccupied_never_actuates_however_hot(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=False,
            ),
        )
        self.assertIs(trace.mode, Mode.UNOCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(any("unoccupied" in r for r in trace.rejected))

    def test_unoccupied_has_no_band(self):
        trace = evaluate_room(
            room(),
            RoomInputs(now=NOW, temperature_c=30.0, relative_humidity=60.0, presence=False),
        )
        self.assertIsNone(trace.band_low)
        self.assertIsNone(trace.band_high)

    def test_heading_home_overrides_unoccupied(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)

    def test_precondition_uses_the_occupied_comfort_band(self):
        """There is one comfort definition per room, and it is the band."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=55.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)
        self.assertEqual(trace.band_low, 25.0)
        self.assertEqual(trace.band_high, 28.0)
        expected = dry_bulb_for_index(ComfortBand(25.0, 28.0).midpoint, 55.0)
        self.assertAlmostEqual(trace.target_dry_bulb_c, expected, places=3)

    def test_room_with_no_bands_never_actuates(self):
        trace = evaluate_room(
            room(bands={}),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=True,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIsNone(trace.band_low)

    def test_heading_home_with_no_occupied_band_does_nothing(self):
        trace = evaluate_room(
            room(bands={Mode.SLEEP: ComfortBand(24.0, 26.0)}),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)
        self.assertIsNone(trace.band_low)
        self.assertIsNone(trace.target_dry_bulb_c)
        self.assertIs(trace.actuator, ActuatorStep.NONE)


class TestTariffEdgeCases(unittest.TestCase):
    def test_whole_day_window_matches_every_hour(self):
        schedule = TariffSchedule(
            (TariffWindow(time(0, 0), time(0, 0), "flat"),)
        )
        for hour in range(24):
            self.assertEqual(schedule.window_at(time(hour, 30)).rate, "flat")

    def test_window_wrapping_midnight_matches_both_sides(self):
        window = TariffWindow(time(22, 0), time(6, 0), "overnight")
        self.assertTrue(window.contains(time(23, 0)))
        self.assertTrue(window.contains(time(2, 0)))
        self.assertFalse(window.contains(time(12, 0)))

    def test_overlapping_windows_are_rejected(self):
        with self.assertRaises(ValueError):
            TariffSchedule(
                (
                    TariffWindow(time(0, 0), time(12, 0), "a"),
                    TariffWindow(time(10, 0), time(0, 0), "b"),
                )
            )


class TestClamping(unittest.TestCase):
    def test_unreachable_target_is_clamped_and_recorded(self):
        """A band the humidity makes unreachable must not command 45 C."""
        trace = evaluate_room(
            room(bands={Mode.OCCUPIED: ComfortBand(44.0, 46.0)}),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=20.0,
                presence=True,
            ),
        )
        self.assertEqual(trace.target_dry_bulb_c, 40.0)
        self.assertTrue(any("clamped" in r for r in trace.rejected))

    def test_normal_target_is_not_flagged_as_clamped(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
            ),
        )
        self.assertFalse(any("clamped" in r for r in trace.rejected))


class TestPrecoolRespectsOccupancy(unittest.TestCase):
    def test_precool_does_not_run_in_an_unoccupied_room(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                precool_opportunity=True,
                forecast_demand_ahead=True,
            ),
        )
        self.assertIs(trace.mode, Mode.UNOCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(any("unoccupied" in r for r in trace.rejected))


class TestSleepWithFailedSensor(unittest.TestCase):
    def test_unknown_presence_at_night_holds_sleep_not_day(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(trace.mode, Mode.SLEEP)
        self.assertEqual(trace.band_low, 24.0)


class TestSleepSchedule(unittest.TestCase):
    def test_sleep_requires_the_schedule_to_be_active(self):
        awake = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                sleep_schedule_active=False,
            ),
        )
        self.assertIs(awake.mode, Mode.OCCUPIED)

        asleep = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(asleep.mode, Mode.SLEEP)
        self.assertEqual(asleep.band_low, 24.0)
