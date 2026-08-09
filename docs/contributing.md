# Contributing

## Running the tests

The pure decision modules import nothing from Home Assistant, so they run with
no dependencies at all:

```
python3 -m unittest discover -s tests -p "test_core.py" -v
```

43 tests covering the comfort index and its inverse, mode precedence, the full
actuator ordering, tariff windows and constraint handling, and setpoint
clamping.

The Home Assistant surface tests need the test harness:

```
pip install pytest-homeassistant-custom-component
pytest tests
```

**Those have never been run.** They are written, not verified.

## Linting and typing

```
ruff check custom_components tests
mypy --config-file mypy.ini custom_components
```

Ruff passes clean. Mypy passes clean on the four pure modules; the rest cannot
be verified without Home Assistant installed, because every `homeassistant.*`
import resolves to `Any`.

## Structure

| File | Home Assistant? |
|---|---|
| `hci.py` | No — the comfort index and its inverse |
| `models.py` | No — modes, room config, inputs, trace |
| `modes.py` | No — mode precedence and actuator selection |
| `tariff.py` | No — windows and constraints |
| `store.py` | Yes — learned state persistence |
| `coordinator.py` | Yes — reads state, runs the evaluator |
| `entity.py` | Yes — base entity and device info |
| `sensor.py` | Yes — the three sensors per room |
| `config_flow.py` | Yes — setup and room configuration |
| `diagnostics.py` | Yes — diagnostics download |

**Decisions belong in the pure modules.** If a change puts a decision in
`coordinator.py`, it has gone in the wrong place — that file gathers inputs and
publishes results, nothing more.

## Quality scale

`custom_components/hvac_coordinator/quality_scale.yaml` tracks all 54 rules from
the Home Assistant integration quality scale, each marked done, todo, or exempt
with a written reason.

Any change that would move a rule backwards should say so in the pull request.

## Adding a configuration option

Don't, unless a user cannot get a correct result without it. Read
[Configuration](configuration.md) first. A new option needs to name the user who
is stuck without it.
