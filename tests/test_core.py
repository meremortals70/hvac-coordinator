"""Tests for the pure modules. No Home Assistant required.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import UTC, datetime, time, timedelta
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
                air_moving=True,
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
                direct_sun=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)
        self.assertEqual(trace.demand, "cool")

    def test_covers_are_not_moved_when_no_sun_is_on_the_room(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                direct_sun=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("no sun on this room" in r for r in trace.rejected))

    def test_fan_when_marginally_above_band(self):
        # Band high is 28.0; the fan margin is 0.5 HCI. 28 C at 35% reads 28.35
        # with the air already moving, which is the case where a fan is the
        # right answer rather than an escalation.
        temp = 28.0
        rh = 35.0
        self.assertGreater(comfort_index(temp, rh), 28.0)
        self.assertLess(comfort_index(temp, rh), 28.5)
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=temp,
                relative_humidity=rh,
                presence=True,
                air_moving=True,
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
                direct_sun=True,
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
                air_moving=True,
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


class TestPrecoolIgnoresPresentOccupancy(unittest.TestCase):
    """Precool banks against a load that is coming, not one that is here.

    The free window is the middle of the day, when the room is usually empty.
    The load it is banking against arrives in the evening. Gating precool on
    someone being in the room now would stop it doing the one job it has.
    """

    def test_precool_runs_in_an_empty_room(self):
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
        self.assertIs(trace.mode, Mode.PRECOOL)
        self.assertIsNot(trace.actuator, ActuatorStep.NONE)

    def test_precool_still_needs_a_load_coming(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                precool_opportunity=True,
                forecast_demand_ahead=False,
            ),
        )
        self.assertIs(trace.mode, Mode.UNOCCUPIED)


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


_forms = importlib.import_module("hvac_core.forms")
_const = importlib.import_module("hvac_core.const")


class TestRoomForm(unittest.TestCase):
    def test_room_id_is_slugged_from_the_name(self):
        room = _forms.room_from_input(
            {"name": "Main Bedroom", "climate_entity_id": "climate.a"}
        )
        self.assertEqual(room["room_id"], "main_bedroom")

    def test_an_unticked_room_carries_no_lockout_reason(self):
        """The reason is set by the lockout step, which requires the tick box."""
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_id": "climate.a"}
        )
        self.assertIsNone(room["lockout_reason"])

    def test_optional_entities_default_to_absent(self):
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_id": "climate.a"}
        )
        self.assertIsNone(room["sleep_schedule_entity_id"])
        self.assertEqual(room["opening_entity_ids"], [])


class TestBandForm(unittest.TestCase):
    def test_only_complete_pairs_are_kept(self):
        bands = _forms.bands_from_input(
            {"occupied_low": 24.0, "occupied_high": 27.0, "sleep_low": 21.0}
        )
        self.assertEqual(set(bands), {"occupied"})

    def test_inverted_band_is_invalid(self):
        self.assertFalse(
            _forms.bands_are_valid({"occupied": {"low": 27.0, "high": 24.0}})
        )

    def test_equal_bounds_are_invalid(self):
        self.assertFalse(
            _forms.bands_are_valid({"occupied": {"low": 24.0, "high": 24.0}})
        )

    def test_defaults_are_seeded_and_valid(self):
        """A fresh room arrives with sensible numbers, not six empty boxes."""
        suggestions = _forms.default_band_suggestions()
        self.assertEqual(suggestions["occupied_low"], 24.0)
        self.assertEqual(suggestions["occupied_high"], 27.0)
        self.assertEqual(suggestions["sleep_low"], 21.0)
        self.assertTrue(_forms.bands_are_valid(_const.DEFAULT_BANDS))

    def test_defaults_have_no_unoccupied_band(self):
        """An unoccupied room is off, so it has no band to seed."""
        self.assertNotIn("unoccupied", _const.DEFAULT_BANDS)

    def test_stored_bands_round_trip_through_the_form(self):
        stored = {"occupied": {"low": 24.0, "high": 27.0}}
        suggestions = _forms.bands_as_suggestions(stored)
        self.assertEqual(_forms.bands_from_input(suggestions), stored)


class TestLockoutReasons(unittest.TestCase):
    def test_built_in_reasons_are_offered(self):
        self.assertIn("Under renovation", _forms.known_lockout_reasons([]))

    def test_a_built_in_reason_is_not_stored_as_custom(self):
        self.assertEqual(
            _forms.extend_lockout_reasons([], {"lockout_reason": "Under renovation"}),
            [],
        )

    def test_a_typed_reason_becomes_available_globally(self):
        stored = _forms.extend_lockout_reasons(
            [], {"lockout_reason": "Waiting on sparky"}
        )
        self.assertEqual(stored, ["Waiting on sparky"])
        self.assertIn("Waiting on sparky", _forms.known_lockout_reasons(stored))

    def test_a_reason_is_not_stored_twice(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(
                ["Waiting on sparky"], {"lockout_reason": "Waiting on sparky"}
            ),
            ["Waiting on sparky"],
        )

    def test_a_room_without_a_reason_leaves_the_list_alone(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(["Waiting on sparky"], {"lockout_reason": None}),
            ["Waiting on sparky"],
        )

    def test_known_reasons_are_deduplicated(self):
        known = _forms.known_lockout_reasons(["Under renovation", "Waiting on sparky"])
        self.assertEqual(known.count("Under renovation"), 1)


class TestTariffForm(unittest.TestCase):
    def test_window_is_normalised_to_full_time_strings(self):
        window = _forms.window_from_input(
            {"start": "11:00", "end": "14:00", "rate": "free"}
        )
        self.assertEqual(window["start"], "11:00:00")
        self.assertEqual(window["end"], "14:00:00")
        self.assertEqual(window["constraints"], [])
        self.assertTrue(window["coasting_permitted"])

    def test_time_objects_are_accepted_too(self):
        window = _forms.window_from_input(
            {"start": time(0, 0), "end": time(6, 0), "rate": "cheap"}
        )
        self.assertEqual(window["start"], "00:00:00")

    def test_constraints_and_coasting_are_carried(self):
        window = _forms.window_from_input(
            {
                "start": "16:00",
                "end": "21:00",
                "rate": "peak",
                "constraints": ["no_grid_import"],
                "coasting_permitted": False,
            }
        )
        self.assertEqual(window["constraints"], ["no_grid_import"])
        self.assertFalse(window["coasting_permitted"])

    def test_a_complete_schedule_reports_no_problems(self):
        windows = [
            {"start": "00:00:00", "end": "12:00:00", "rate": "a"},
            {"start": "12:00:00", "end": "00:00:00", "rate": "b"},
        ]
        self.assertEqual(_forms.schedule_gaps(windows), [])

    def test_a_gap_is_described(self):
        windows = [
            {"start": "00:00:00", "end": "06:00:00", "rate": "a"},
            {"start": "07:00:00", "end": "00:00:00", "rate": "b"},
        ]
        problems = _forms.schedule_gaps(windows)
        self.assertTrue(any("gap" in p for p in problems))

    def test_a_missing_start_of_day_is_described(self):
        windows = [{"start": "06:00:00", "end": "00:00:00", "rate": "a"}]
        problems = _forms.schedule_gaps(windows)
        self.assertTrue(any("00:00" in p for p in problems))

    def test_a_missing_end_of_day_is_described(self):
        windows = [{"start": "00:00:00", "end": "22:00:00", "rate": "a"}]
        problems = _forms.schedule_gaps(windows)
        self.assertTrue(any("midnight" in p for p in problems))

    def test_windows_sort_by_start(self):
        windows = [
            {"start": "16:00:00", "end": "00:00:00", "rate": "b"},
            {"start": "00:00:00", "end": "16:00:00", "rate": "a"},
        ]
        self.assertEqual(
            [w["rate"] for w in _forms.sort_windows(windows)], ["a", "b"]
        )

    def test_window_description_is_readable(self):
        described = _forms.describe_window(
            {
                "start": "16:00:00",
                "end": "21:00:00",
                "rate": "peak",
                "constraints": ["no_grid_import"],
            }
        )
        self.assertIn("16:00", described)
        self.assertIn("peak", described)
        self.assertIn("no_grid_import", described)

    def test_custom_rate_label_is_stored_once(self):
        stored = _forms.extend_rate_labels([], "solar_soak")
        self.assertEqual(stored, ["solar_soak"])
        self.assertEqual(_forms.extend_rate_labels(stored, "solar_soak"), stored)

    def test_built_in_rate_label_is_not_stored(self):
        self.assertEqual(_forms.extend_rate_labels([], "peak"), [])


class TestTariffFormFeedsTheSchedule(unittest.TestCase):
    """What the form produces must be what TariffSchedule accepts."""

    def test_configured_windows_build_a_valid_schedule(self):
        raw = [
            _forms.window_from_input(
                {"start": "00:00", "end": "06:00", "rate": "cheap",
                 "coasting_permitted": False}
            ),
            _forms.window_from_input(
                {"start": "06:00", "end": "16:00", "rate": "standard"}
            ),
            _forms.window_from_input(
                {"start": "16:00", "end": "00:00", "rate": "peak",
                 "constraints": ["no_grid_import"]}
            ),
        ]
        self.assertEqual(_forms.schedule_gaps(raw), [])

        schedule = TariffSchedule(
            tuple(
                TariffWindow(
                    start=time.fromisoformat(w["start"]),
                    end=time.fromisoformat(w["end"]),
                    rate=w["rate"],
                    constraints=frozenset(w["constraints"]),
                    coasting_permitted=w["coasting_permitted"],
                )
                for w in raw
            )
        )
        self.assertFalse(schedule.window_at(time(3, 0)).coasting_permitted)
        self.assertIn("no_grid_import", schedule.window_at(time(18, 0)).constraints)
        self.assertEqual(schedule.unrecognised_constraints(), frozenset())


class TestCoversEscalate(unittest.TestCase):
    """Covers must hand over once they have nowhere useful left to go."""

    def _hot_room(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 35.0,
            "presence": True,
            "has_covers": True,
            "direct_sun": True,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_open_covers_are_used_first(self):
        trace = self._hot_room(cover_position=100.0)
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_already_closed_covers_escalate_to_the_next_step(self):
        trace = self._hot_room(cover_position=0.0)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("already closed" in r for r in trace.rejected))

    def test_nearly_closed_covers_count_as_closed(self):
        trace = self._hot_room(cover_position=3.0)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)

    def test_unknown_position_still_commands_once(self):
        """No reported position is not a reason to skip the cheapest step."""
        trace = self._hot_room(cover_position=None)
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_already_open_covers_escalate_when_heating(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                direct_sun=True,
                cover_position=100.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("already open" in r for r in trace.rejected))

    def test_partly_open_covers_are_still_worth_closing(self):
        trace = self._hot_room(cover_position=60.0)
        self.assertIs(trace.actuator, ActuatorStep.COVERS)


class TestSemiTransparentBlinds(unittest.TestCase):
    """Light level cannot gate covers: a sheer blind reads bright when shut."""

    def test_a_bright_room_with_no_sun_on_it_does_not_move_covers(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=0.0,
                # Sheer blind fully closed, room still bright.
                illuminance_lux=18000.0,
                direct_sun=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("no sun on this room" in r for r in trace.rejected))

    def test_a_dim_room_with_sun_on_it_still_moves_covers(self):
        """Blackout blind open at dawn: low lux, sun genuinely on the glass."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=100.0,
                illuminance_lux=40.0,
                direct_sun=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_unknown_sun_does_not_move_covers(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=100.0,
                direct_sun=None,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("cannot tell whether the sun" in r for r in trace.rejected))


class TestUnitCapabilities(unittest.TestCase):
    """The decision must never choose a mode the unit does not have."""

    def _hot(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 80.0,
            "presence": True,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_dry_is_skipped_on_a_unit_without_it(self):
        trace = self._hot(can_dry=False)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("no dry mode" in r for r in trace.rejected))

    def test_fan_is_skipped_on_a_unit_without_it(self):
        # Marginally above band, where fan would otherwise be chosen.
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=35.0,
                presence=True,
                can_fan_only=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.FAN)
        self.assertTrue(any("no fan-only mode" in r for r in trace.rejected))

    def test_a_cooling_only_unit_does_nothing_when_asked_to_heat(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                can_heat=False,
            ),
        )
        self.assertEqual(trace.demand, "heat")
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(any("cannot heat" in r for r in trace.rejected))

    def test_a_heating_only_unit_does_nothing_when_asked_to_cool(self):
        trace = self._hot(can_cool=False, can_dry=False)
        self.assertEqual(trace.demand, "cool")
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(any("cannot cool" in r for r in trace.rejected))

    def test_an_unavailable_unit_actuates_nothing(self):
        trace = self._hot(
            can_cool=False, can_heat=False, can_dry=False, can_fan_only=False
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)


_hci = importlib.import_module("hvac_core.hci")
_thermal = importlib.import_module("hvac_core.thermal")
_forecast = importlib.import_module("hvac_core.forecast")


def _interval(**overrides):
    base = {
        "elapsed_hours": 0.25,
        "indoor_start_c": 24.0,
        "indoor_end_c": 24.0,
        "humidity_start": 60.0,
        "humidity_end": 60.0,
        "outdoor_c": 30.0,
        "direct_sun": False,
        "compressor": 0,
        "drying": False,
    }
    base.update(overrides)
    return _thermal.Observation(**base)


class TestThermalLearning(unittest.TestCase):
    def test_a_fresh_model_refuses_to_predict(self):
        """Day one: no prediction, so the caller falls back to hysteresis."""
        model = _thermal.ThermalModel()
        self.assertFalse(model.converged)
        self.assertIsNone(model.drift_rate(24.0, 30.0, direct_sun=False))
        self.assertIsNone(
            model.holds_through(
                24.0, 30.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=26.0
            )
        )

    def test_intervals_that_are_too_short_or_long_are_ignored(self):
        model = _thermal.ThermalModel()
        model.observe(_interval(elapsed_hours=0.0001))
        model.observe(_interval(elapsed_hours=5.0))
        self.assertEqual(model.k_loss.samples, 0)

    def test_heat_loss_converges_from_passive_intervals(self):
        model = _thermal.ThermalModel()
        # Outdoor 6 C above indoor, room gains 0.6 C/h -> k_loss 0.1
        for _ in range(60):
            model.observe(
                _interval(
                    indoor_start_c=24.0,
                    indoor_end_c=24.15,
                    outdoor_c=30.0,
                )
            )
        self.assertTrue(model.k_loss.converged)
        self.assertAlmostEqual(model.k_loss.value, 0.1, places=1)

    def test_compressor_authority_is_learned_only_while_it_runs(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(
                _interval(
                    indoor_start_c=26.0,
                    indoor_end_c=25.5,
                    compressor=-1,
                )
            )
        self.assertTrue(model.k_sensible.converged)
        self.assertAlmostEqual(model.k_sensible.value, 2.0, places=1)
        # A compressor interval must teach nothing about passive loss.
        self.assertEqual(model.k_loss.samples, 0)

    def test_a_room_moving_against_the_compressor_teaches_nothing(self):
        """Door open, or a heat load. Not the unit's fault, not its lesson."""
        model = _thermal.ThermalModel()
        for _ in range(30):
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=26.5, compressor=-1)
            )
        self.assertEqual(model.k_sensible.samples, 0)

    def test_latent_is_learned_separately_from_sensible(self):
        """The whole reason this model is not a heating-climate model.

        A rainy interval: dry bulb falls while humidity climbs. Sensible load
        drops as latent load rises, and one coefficient cannot describe both.
        """
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(
                _interval(
                    humidity_start=80.0,
                    humidity_end=78.0,
                    drying=True,
                )
            )
        self.assertTrue(model.k_latent.converged)
        self.assertAlmostEqual(model.k_latent.value, 8.0, places=0)
        # Drying taught nothing about the sensible term.
        self.assertEqual(model.k_sensible.samples, 0)

    def test_level_indoor_and_outdoor_teaches_nothing(self):
        """Nothing driving means the residual is noise over nearly zero."""
        model = _thermal.ThermalModel()
        for _ in range(30):
            model.observe(_interval(indoor_start_c=24.0, outdoor_c=24.0))
        self.assertEqual(model.k_loss.samples, 0)

    def test_the_filter_keeps_listening_after_converging(self):
        """Process noise: a house changes, and a locked filter is wrong."""
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
        settled = model.k_loss.value
        for _ in range(120):
            model.observe(_interval(indoor_end_c=24.3, outdoor_c=30.0))
        self.assertGreater(model.k_loss.value, settled)


class TestThermalPrediction(unittest.TestCase):
    def _converged(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=25.5, compressor=-1)
            )
        return model

    def test_a_room_holds_when_the_drift_is_small(self):
        model = self._converged()
        self.assertTrue(
            model.holds_through(
                24.0, 25.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=27.0
            )
        )

    def test_a_room_does_not_hold_against_a_big_difference(self):
        model = self._converged()
        self.assertFalse(
            model.holds_through(
                24.0, 40.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=24.5
            )
        )

    def test_no_outdoor_reading_means_no_prediction(self):
        model = self._converged()
        self.assertIsNone(model.drift_rate(24.0, None, direct_sun=False))

    def test_pull_down_time_accounts_for_the_room_fighting_back(self):
        """On a hot day the unit fights the drift, so it takes longer."""
        model = self._converged()
        mild = model.hours_to_reach(28.0, 25.0, 26.0, direct_sun=False)
        hot = model.hours_to_reach(28.0, 25.0, 40.0, direct_sun=False)
        self.assertIsNotNone(mild)
        self.assertIsNotNone(hot)
        self.assertGreater(hot, mild)

    def test_a_target_already_reached_takes_no_time(self):
        model = self._converged()
        self.assertEqual(model.hours_to_reach(25.0, 25.0, 30.0, direct_sun=False), 0.0)

    def test_energy_rises_with_a_harder_job(self):
        model = self._converged()
        easy = model.energy_for(
            26.0, 25.0, 26.0, direct_sun=False, hours=4.0, rated_kw=1.2
        )
        hard = model.energy_for(
            32.0, 25.0, 38.0, direct_sun=False, hours=4.0, rated_kw=1.2
        )
        self.assertLess(easy, hard)


class TestThermalPersistence(unittest.TestCase):
    def test_a_model_survives_a_round_trip(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
        restored = _thermal.ThermalModel.from_dict(model.as_dict())
        self.assertAlmostEqual(restored.k_loss.value, model.k_loss.value)
        self.assertEqual(restored.k_loss.samples, model.k_loss.samples)
        self.assertTrue(restored.k_loss.converged)

    def test_unreadable_stored_state_starts_fresh(self):
        """Losing this costs convergence time, not correctness."""
        for junk in (None, "corrupt", {"k_loss": "not a dict"}, {}):
            model = _thermal.ThermalModel.from_dict(junk)
            self.assertFalse(model.k_loss.converged)
            self.assertEqual(model.k_loss.samples, 0)

    def test_diagnostics_name_every_coefficient(self):
        diagnostics = _thermal.ThermalModel().diagnostics()
        self.assertEqual(
            set(diagnostics), {"k_loss", "k_solar", "k_sensible", "k_latent"}
        )
        self.assertIn("converged", diagnostics["k_loss"])


class TestDemandForecast(unittest.TestCase):
    """The published contract. No vendor concepts may appear in it."""

    def _room_input(self, **overrides):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=25.5, compressor=-1)
            )
        base = {
            "room_id": "office",
            "model": model,
            "indoor_c": 28.0,
            "target_c": 25.0,
            "outdoor_c": 33.0,
            "direct_sun": False,
            "will_run": True,
        }
        base.update(overrides)
        return _forecast.RoomForecastInput(**base)

    def test_a_room_that_will_not_run_contributes_nothing(self):
        projection = _forecast.project_room(
            self._room_input(will_run=False), horizon_hours=8
        )
        self.assertEqual(projection.kwh, 0.0)
        self.assertIn("will not run", projection.reason)

    def test_an_unconverged_room_is_flagged_not_hidden(self):
        fresh = self._room_input()
        fresh = _forecast.RoomForecastInput(
            room_id=fresh.room_id,
            model=_thermal.ThermalModel(),
            indoor_c=28.0,
            target_c=25.0,
            outdoor_c=33.0,
            direct_sun=False,
            will_run=True,
        )
        projection = _forecast.project_room(fresh, horizon_hours=8)
        self.assertFalse(projection.modelled)
        self.assertGreater(projection.kwh, 0.0)
        self.assertIn("not converged", projection.reason)

    def test_no_reading_projects_nothing_and_says_so(self):
        projection = _forecast.project_room(
            self._room_input(indoor_c=None), horizon_hours=8
        )
        self.assertEqual(projection.kwh, 0.0)
        self.assertFalse(projection.modelled)

    def test_the_forecast_carries_no_vendor_concepts(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset({"no_grid_import"})),
            ],
            horizon_hours=8,
        )
        text = repr(forecast.as_attributes()).lower()
        for vendor in ("powerwall", "tesla", "sungrow", "fronius", "byd", "reserve"):
            self.assertNotIn(vendor, text)

    def test_windows_are_broken_out_and_carry_their_constraints(self):
        forecast = _forecast.build_forecast(
            NOW,  # 14:30
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset({"no_grid_import"})),
            ],
            horizon_hours=8,
        )
        rates = {window.rate for window in forecast.windows}
        self.assertEqual(rates, {"standard", "peak"})
        peak = next(w for w in forecast.windows if w.rate == "peak")
        self.assertIn("no_grid_import", peak.constraints)
        self.assertAlmostEqual(peak.hours, 6.5, places=1)

    def test_window_hours_sum_to_the_horizon(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset()),
            ],
            horizon_hours=8,
        )
        self.assertAlmostEqual(
            sum(window.hours for window in forecast.windows), 8.0, places=1
        )

    def test_window_energy_sums_to_the_total(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input(), self._room_input(room_id="living")],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset()),
            ],
            horizon_hours=8,
        )
        self.assertAlmostEqual(
            sum(window.kwh for window in forecast.windows),
            forecast.total_kwh,
            places=1,
        )

    def test_a_wrapping_window_is_counted_correctly(self):
        forecast = _forecast.build_forecast(
            NOW,  # 14:30, horizon to 22:30
            [self._room_input()],
            [(time(21, 0), time(9, 0), "overnight", frozenset())],
            horizon_hours=8,
        )
        self.assertAlmostEqual(forecast.windows[0].hours, 1.5, places=1)

    def test_no_tariff_configured_still_produces_a_total(self):
        forecast = _forecast.build_forecast(NOW, [self._room_input()], [], 8)
        self.assertGreater(forecast.total_kwh, 0.0)
        self.assertEqual(forecast.windows, [])

    def test_fully_modelled_is_false_when_any_room_is_guessing(self):
        fresh = _forecast.RoomForecastInput(
            room_id="guest",
            model=_thermal.ThermalModel(),
            indoor_c=28.0,
            target_c=25.0,
            outdoor_c=33.0,
            direct_sun=False,
            will_run=True,
        )
        forecast = _forecast.build_forecast(NOW, [self._room_input(), fresh], [], 8)
        self.assertFalse(forecast.fully_modelled)


_sun = importlib.import_module("hvac_core.sun")


class TestSunGeometry(unittest.TestCase):
    """Sun on the glass, from position and window direction. No sensor needed."""

    def test_a_north_window_gets_midday_sun_in_the_southern_hemisphere(self):
        # Brisbane midday: sun due north, high.
        self.assertTrue(_sun.sun_on_window(0.0, 60.0, _sun.WINDOW_DIRECTIONS["north"]))

    def test_a_south_window_does_not_get_that_sun(self):
        self.assertFalse(_sun.sun_on_window(0.0, 60.0, _sun.WINDOW_DIRECTIONS["south"]))

    def test_a_west_window_gets_the_afternoon(self):
        self.assertTrue(_sun.sun_on_window(270.0, 25.0, _sun.WINDOW_DIRECTIONS["west"]))

    def test_a_west_window_does_not_get_the_morning(self):
        self.assertFalse(_sun.sun_on_window(90.0, 25.0, _sun.WINDOW_DIRECTIONS["west"]))

    def test_the_sun_below_the_horizon_is_on_no_window(self):
        for direction in _sun.WINDOW_DIRECTIONS.values():
            self.assertFalse(_sun.sun_on_window(180.0, -5.0, direction))

    def test_a_sun_barely_up_does_not_count(self):
        """One degree of elevation is not worth moving a blind for."""
        self.assertFalse(_sun.sun_on_window(90.0, 1.0, _sun.WINDOW_DIRECTIONS["east"]))

    def test_the_edge_of_the_acceptance_angle(self):
        east = _sun.WINDOW_DIRECTIONS["east"]
        self.assertTrue(_sun.sun_on_window(0.0, 30.0, east))
        self.assertFalse(_sun.sun_on_window(359.0, 30.0, east))

    def test_wrapping_past_north_is_handled(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(_sun.sun_on_window(350.0, 30.0, north))
        self.assertTrue(_sun.sun_on_window(10.0, 30.0, north))

    def test_no_direction_configured_means_no_answer(self):
        """Not 'no sun'. The evaluator must not move covers on a guess."""
        self.assertIsNone(_sun.sun_on_window(180.0, 45.0, None))

    def test_no_sun_position_means_no_answer(self):
        self.assertIsNone(_sun.sun_on_window(None, None, 0.0))

    def test_every_offered_direction_resolves(self):
        for name in _sun.WINDOW_DIRECTIONS:
            self.assertIsNotNone(_sun.azimuth_for_direction(name))
        self.assertIsNone(_sun.azimuth_for_direction("upwards"))


class TestLockoutIsOneField(unittest.TestCase):
    """One dropdown answers both questions: no toggle, no second screen."""

    def test_not_locked_out_stores_no_reason(self):
        room = _forms.room_from_input(
            {
                "name": "Office",
                "climate_entity_id": "climate.o",
                "lockout_reason": _const.NOT_LOCKED_OUT,
            }
        )
        self.assertIsNone(room["lockout_reason"])

    def test_choosing_a_reason_locks_the_room_out(self):
        room = _forms.room_from_input(
            {
                "name": "Study",
                "climate_entity_id": "climate.s",
                "lockout_reason": "Under renovation",
            }
        )
        self.assertEqual(room["lockout_reason"], "Under renovation")

    def test_the_not_locked_out_option_comes_first(self):
        options = _forms.known_lockout_reasons([])
        self.assertEqual(options[0], _const.NOT_LOCKED_OUT)

    def test_not_locked_out_is_never_stored_as_a_custom_reason(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(
                [], {"lockout_reason": _const.NOT_LOCKED_OUT}
            ),
            [],
        )

    def test_blank_is_treated_as_not_locked_out(self):
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_id": "climate.o", "lockout_reason": "  "}
        )
        self.assertIsNone(room["lockout_reason"])


_grace = importlib.import_module("hvac_core.grace")
_tariff_mod = importlib.import_module("hvac_core.tariff")


class TestOccupancyGrace(unittest.TestCase):
    """Raw presence is the wrong signal for a compressor."""

    def setUp(self):
        self.state = _grace.GraceState()
        self.settings = _grace.GraceSettings()
        self.t = NOW

    def _step(self, present, minutes=0):
        self.t = self.t + timedelta(minutes=minutes)
        return _grace.evaluate_grace(self.state, present, self.t, self.settings)

    def test_a_grab_and_go_visit_never_starts_the_room(self):
        """Someone drops a laptop off and leaves. No compressor start."""
        self.assertFalse(self._step(True).occupied)
        self.assertFalse(self._step(True, 1).occupied)
        self.assertFalse(self._step(False, 0.5).occupied)
        self.assertFalse(self.state.occupied)

    def test_sustained_presence_starts_the_room(self):
        self.assertFalse(self._step(True).occupied)
        self.assertTrue(self._step(True, 2).occupied)

    def test_answering_the_front_door_does_not_stop_the_room(self):
        """The delivery case. Five minutes away must not shut the room down."""
        self._step(True)
        self.assertTrue(self._step(True, 2).occupied)
        self._step(False)
        self.assertTrue(self._step(False, 2).occupied)
        result = self._step(False, 3)
        self.assertTrue(result.occupied)
        self.assertIn("holding in case they return", result.reason)

    def test_returning_resets_the_absence(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self._step(False, 8)
        self.assertTrue(self._step(True, 1).occupied)
        # Away again: the clock restarts, so 8 minutes is not cumulative.
        self._step(False)
        self.assertTrue(self._step(False, 8).occupied)

    def test_a_long_absence_finally_stops_the_room(self):
        self._step(True)
        self._step(True, 2)
        # The vacancy clock starts when they leave, so departure is registered
        # first and the elapsed time is measured from there.
        self._step(False)
        self.assertFalse(self._step(False, 11).occupied)

    def test_a_returning_occupant_starts_again_without_waiting_twice(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self._step(False, 11)
        self.assertFalse(self._step(True, 0.5).occupied)
        self.assertTrue(self._step(True, 2).occupied)

    def test_unknown_presence_holds_whatever_the_room_was(self):
        self._step(True)
        self._step(True, 2)
        result = self._step(None, 30)
        self.assertTrue(result.occupied)
        self.assertIn("presence unknown", result.reason)

    def test_unknown_presence_does_not_start_an_empty_room(self):
        self.assertFalse(self._step(None, 30).occupied)


class TestGraceAnnouncements(unittest.TestCase):
    def setUp(self):
        self.state = _grace.GraceState()
        self.settings = _grace.GraceSettings(announce=True)
        self.t = NOW

    def _step(self, present, minutes=0):
        self.t = self.t + timedelta(minutes=minutes)
        return _grace.evaluate_grace(self.state, present, self.t, self.settings)

    def test_two_warnings_before_shutting_down(self):
        self._step(True)
        self._step(True, 2)

        self._step(False)
        first = self._step(False, 11)
        self.assertIs(first.announcement, _grace.Announcement.FIRST_WARNING)
        self.assertTrue(first.occupied, "must not shut off on the first warning")

        quiet = self._step(False, 1)
        self.assertIs(quiet.announcement, _grace.Announcement.NONE)
        self.assertTrue(quiet.occupied)

        final = self._step(False, 3)
        self.assertIs(final.announcement, _grace.Announcement.FINAL_WARNING)
        self.assertFalse(final.occupied)

    def test_coming_back_after_the_warning_cancels_the_shutdown(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self.assertIs(
            self._step(False, 11).announcement, _grace.Announcement.FIRST_WARNING
        )
        self.assertTrue(self._step(True, 1).occupied)
        self.assertIsNone(self.state.warned_at)
        # And a fresh absence warns again rather than shutting off silently.
        self._step(False)
        self.assertIs(
            self._step(False, 11).announcement, _grace.Announcement.FIRST_WARNING
        )

    def test_announcements_off_shuts_down_without_speaking(self):
        self.settings = _grace.GraceSettings(announce=False)
        self._step(True)
        self._step(True, 2)
        self._step(False)
        result = self._step(False, 11)
        self.assertFalse(result.occupied)
        self.assertIs(result.announcement, _grace.Announcement.NONE)

    def test_defaults_are_sensible_out_of_the_box(self):
        defaults = _grace.GraceSettings()
        self.assertEqual(defaults.occupied_after, timedelta(minutes=2))
        self.assertEqual(defaults.vacant_after, timedelta(minutes=10))
        self.assertEqual(defaults.warning_grace, timedelta(minutes=3))
        self.assertFalse(defaults.announce, "a house should not start talking")

    def test_settings_come_from_minutes(self):
        settings = _grace.GraceSettings.from_minutes(5, 20, 2, True)
        self.assertEqual(settings.occupied_after, timedelta(minutes=5))
        self.assertEqual(settings.vacant_after, timedelta(minutes=20))
        self.assertTrue(settings.announce)

    def test_missing_values_fall_back_to_defaults(self):
        settings = _grace.GraceSettings.from_minutes()
        self.assertEqual(settings.vacant_after, timedelta(minutes=10))


class TestRadiantComfort(unittest.TestCase):
    """Air temperature and humidity cannot see sun, still air or equipment."""

    def test_sun_through_glass_raises_the_index(self):
        shaded = comfort_index(24.0, 60.0)
        sunlit = comfort_index(24.0, 60.0, radiant=1.0)
        self.assertGreater(sunlit, shaded)

    def test_a_half_closed_blind_passes_about_half(self):
        """A 50% blind is not 'no sun'. This is the case that was wrong."""
        fraction = _hci.radiant_load(
            direct_sun=True, cover_position=50.0, has_covers=True
        )
        self.assertGreater(fraction, 0.4)
        self.assertLess(fraction, 0.7)

    def test_a_closed_blind_still_passes_some(self):
        """It absorbs the energy and re-radiates it inward."""
        fraction = _hci.radiant_load(
            direct_sun=True, cover_position=0.0, has_covers=True
        )
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 0.3)

    def test_no_sun_means_no_radiant_load_whatever_the_blind(self):
        for position in (0.0, 50.0, 100.0):
            self.assertEqual(
                _hci.radiant_load(
                    direct_sun=False, cover_position=position, has_covers=True
                ),
                0.0,
            )

    def test_a_room_with_no_covers_takes_all_of_it(self):
        self.assertEqual(
            _hci.radiant_load(direct_sun=True, cover_position=None, has_covers=False),
            1.0,
        )

    def test_still_air_and_heat_load_each_raise_the_index(self):
        base = comfort_index(24.0, 60.0)
        self.assertGreater(comfort_index(24.0, 60.0, still_air=True), base)
        self.assertGreater(comfort_index(24.0, 60.0, heat_load=True), base)

    def test_the_office_case(self):
        """Sitting in a sunlit room behind a half blind, no airflow, PC on.

        The air-only index calls this comfortable. The corrected index does
        not, which is the whole reason the corrections exist.
        """
        radiant = _hci.radiant_load(
            direct_sun=True, cover_position=50.0, has_covers=True
        )
        air_only = comfort_index(24.0, 60.0)
        felt = comfort_index(
            24.0, 60.0, radiant=radiant, still_air=True, heat_load=True
        )
        self.assertLess(air_only, 27.5, "air-only index reads as comfortable")
        self.assertGreater(felt, 27.5, "corrected index reads as warm")

    def test_a_sunlit_room_is_asked_for_colder_air(self):
        shaded = dry_bulb_for_index(25.5, 60.0)
        sunlit = dry_bulb_for_index(25.5, 60.0, radiant=1.0, still_air=True)
        self.assertLess(sunlit, shaded)

    def test_the_inverse_round_trips_with_corrections(self):
        for radiant in (0.0, 0.5, 1.0):
            for still in (True, False):
                target = dry_bulb_for_index(
                    26.0, 60.0, radiant=radiant, still_air=still
                )
                self.assertAlmostEqual(
                    comfort_index(target, 60.0, radiant=radiant, still_air=still),
                    26.0,
                    places=2,
                )

    def test_the_trace_shows_what_the_corrections_added(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=60.0,
                presence=True,
                has_covers=True,
                cover_position=50.0,
                direct_sun=True,
                heat_load=True,
            ),
        )
        self.assertIsNotNone(trace.hci_base)
        self.assertGreater(trace.hci, trace.hci_base)
        self.assertTrue(any("index raised" in r for r in trace.reasons))
        attributes = trace.as_attributes()
        self.assertIn("hci_air_only", attributes)
        self.assertIn("radiant_fraction", attributes)


class TestTariffCosts(unittest.TestCase):
    """Prices are carried and reported; no decision depends on them."""

    def _schedule(self):
        return TariffSchedule(
            (
                TariffWindow(time(0, 0), time(16, 0), "off_peak", import_cents=22.5),
                TariffWindow(time(16, 0), time(0, 0), "peak", import_cents=48.9),
            ),
            (_tariff_mod.ExportWindow(time(0, 0), time(0, 0), 5.0),),
            daily_supply_cents=118.0,
        )

    def test_import_price_varies_by_window(self):
        schedule = self._schedule()
        self.assertEqual(schedule.import_cents_at(time(9, 0)), 22.5)
        self.assertEqual(schedule.import_cents_at(time(18, 0)), 48.9)

    def test_a_flat_feed_in_applies_all_day(self):
        schedule = self._schedule()
        for hour in range(24):
            self.assertEqual(schedule.export_cents_at(time(hour, 0)), 5.0)

    def test_feed_in_can_vary_by_window(self):
        schedule = TariffSchedule(
            (TariffWindow(time(0, 0), time(0, 0), "flat"),),
            (
                _tariff_mod.ExportWindow(time(0, 0), time(12, 0), 12.0),
                _tariff_mod.ExportWindow(time(12, 0), time(0, 0), 3.0),
            ),
        )
        self.assertEqual(schedule.export_cents_at(time(9, 0)), 12.0)
        self.assertEqual(schedule.export_cents_at(time(15, 0)), 3.0)

    def test_no_feed_in_configured_reports_none(self):
        schedule = TariffSchedule((TariffWindow(time(0, 0), time(0, 0), "flat"),))
        self.assertIsNone(schedule.export_cents_at(time(9, 0)))

    def test_daily_supply_charge_is_carried(self):
        self.assertEqual(self._schedule().daily_supply_cents, 118.0)

    def test_a_window_without_a_price_is_still_valid(self):
        """Prices are optional: the controller's decisions do not use them."""
        schedule = TariffSchedule((TariffWindow(time(0, 0), time(0, 0), "flat"),))
        self.assertIsNone(schedule.import_cents_at(time(9, 0)))


class TestTariffEditing(unittest.TestCase):
    def test_a_window_round_trips_through_the_edit_form(self):
        original = _forms.window_from_input(
            {
                "start": "16:00",
                "end": "21:00",
                "rate": "peak",
                "import_cents_per_kwh": 48.9,
                "constraints": ["no_grid_import"],
                "coasting_permitted": False,
            }
        )
        suggestions = _forms.window_as_suggestions(original)
        self.assertEqual(_forms.window_from_input(suggestions), original)

    def test_the_price_appears_in_the_window_description(self):
        described = _forms.describe_window(
            {
                "start": "16:00:00",
                "end": "21:00:00",
                "rate": "peak",
                "import_cents_per_kwh": 48.9,
            }
        )
        self.assertIn("48.9", described)

    def test_a_flat_export_window_covers_the_whole_day(self):
        window = _forms.export_window_from_input({"export_cents_per_kwh": 5.0})
        self.assertEqual(window["start"], window["end"])
        self.assertIn("all day", _forms.describe_export_window(window))

    def test_a_partial_export_window_is_described_by_its_span(self):
        window = _forms.export_window_from_input(
            {"export_cents_per_kwh": 12.0, "start": "06:00", "end": "12:00"}
        )
        described = _forms.describe_export_window(window)
        self.assertIn("06:00", described)
        self.assertIn("12.0", described)


class TestOverhangShading(unittest.TestCase):
    """An eave shades a window whenever the sun is high. Ignoring it is wrong."""

    def test_a_typical_eave_shades_from_about_two_thirds_up(self):
        cutoff = _sun.shading_elevation(0.9, 2.1)
        self.assertGreater(cutoff, 60.0)
        self.assertLess(cutoff, 72.0)

    def test_a_deeper_eave_shades_from_lower(self):
        shallow = _sun.shading_elevation(0.5, 2.1)
        deep = _sun.shading_elevation(1.5, 2.1)
        self.assertGreater(shallow, deep)

    def test_no_overhang_described_means_no_shading(self):
        self.assertIsNone(_sun.shading_elevation(None, 2.1))
        self.assertIsNone(_sun.shading_elevation(0, 2.1))

    def test_a_high_summer_sun_is_shaded_by_the_eave(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertFalse(
            _sun.sun_on_window(
                0.0, 78.0, north, overhang_projection_m=0.9, overhang_height_m=2.1
            )
        )

    def test_a_low_winter_sun_reaches_under_the_eave(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(
            _sun.sun_on_window(
                0.0, 35.0, north, overhang_projection_m=0.9, overhang_height_m=2.1
            )
        )

    def test_without_an_overhang_the_high_sun_still_counts(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(_sun.sun_on_window(0.0, 78.0, north))

    def test_oblique_sun_slips_under_an_eave_that_would_shade_it_head_on(self):
        """The eave projects less usefully when the sun is off to one side."""
        west = _sun.WINDOW_DIRECTIONS["west"]
        head_on = _sun.sun_on_window(
            270.0, 70.0, west, overhang_projection_m=0.9, overhang_height_m=2.1
        )
        oblique = _sun.sun_on_window(
            200.0, 70.0, west, overhang_projection_m=0.9, overhang_height_m=2.1
        )
        self.assertFalse(head_on)
        self.assertTrue(oblique)


class TestConfigurationIsReadable(unittest.TestCase):
    """A configuration you must edit to inspect is one nobody checks."""

    def _room(self, **overrides):
        base = {
            "room_id": "office",
            "name": "Office",
            "climate_entity_id": "climate.office",
            "temperature_entity_id": "sensor.office_temp",
            "bands": {"occupied": {"low": 24.0, "high": 27.0}},
            "occupied_after_minutes": 2,
            "vacant_after_minutes": 10,
        }
        base.update(overrides)
        return base

    def test_a_room_summary_names_what_is_set_and_what_is_not(self):
        described = _forms.describe_room(self._room())
        self.assertIn("Office", described)
        self.assertIn("climate.office", described)
        self.assertIn("sensor.office_temp", described)
        self.assertIn("Humidity: —", described)
        self.assertIn("Overhang: none", described)

    def test_a_locked_out_room_says_so_prominently(self):
        described = _forms.describe_room(
            self._room(lockout_reason="Under renovation")
        )
        self.assertIn("LOCKED OUT", described)
        self.assertIn("Under renovation", described)

    def test_a_room_with_no_bands_is_flagged(self):
        described = _forms.describe_room(self._room(bands={}))
        self.assertIn("never be actuated", described)

    def test_an_overhang_is_described_with_both_measurements(self):
        described = _forms.describe_room(
            self._room(overhang_projection_m=0.9, overhang_height_m=2.1)
        )
        self.assertIn("0.9", described)
        self.assertIn("2.1", described)

    def test_the_full_summary_separates_rooms_tariff_and_house(self):
        summary = _forms.describe_configuration(
            [self._room()],
            [
                {
                    "start": "00:00:00",
                    "end": "00:00:00",
                    "rate": "flat",
                    "import_cents_per_kwh": 30.0,
                }
            ],
            [{"start": "00:00:00", "end": "00:00:00", "export_cents_per_kwh": 5.0}],
            118.0,
            "sensor.outdoor",
        )
        self.assertIn("**Rooms**", summary)
        self.assertIn("**Tariff**", summary)
        self.assertIn("whole house", summary)
        self.assertIn("**House**", summary)
        self.assertIn("118.0c", summary)
        self.assertIn("sensor.outdoor", summary)

    def test_an_empty_installation_says_so_rather_than_showing_nothing(self):
        summary = _forms.describe_configuration([], [], [], None, None)
        self.assertIn("None configured", summary)
        self.assertIn("No windows configured", summary)
        self.assertIn("Feed-in: not configured", summary)

    def test_an_incomplete_tariff_is_called_out_in_the_summary(self):
        summary = _forms.describe_configuration(
            [],
            [{"start": "06:00:00", "end": "18:00:00", "rate": "day"}],
            [],
            None,
            None,
        )
        self.assertIn("Incomplete", summary)
