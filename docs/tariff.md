# Tariff

## Configuring it

**Settings → Devices & Services → HVAC Coordinator → Configure.** Five tariff
options there:

| Option | What it does |
|---|---|
| Add a tariff window | One window at a time |
| Edit a tariff window | Change an existing one, prefilled |
| Remove a tariff window | |
| Feed-in rate | What you are paid for export |
| Daily supply charge | The fixed connection charge |

The add and edit forms show the schedule as it stands and name any gap or
overlap, so an incomplete schedule tells you what is missing rather than
failing silently.

Configuring no tariff is valid — the controller runs on comfort alone.

## Windows

A tariff is a list of windows. Each window carries:

| Field | Meaning |
|---|---|
| Start | Inclusive |
| End | Exclusive |
| Rate | A label — `cheap`, `free`, `peak`, whatever you use |
| Import price | Cents per kWh, optional |
| Constraints | Absolute rules that apply during this window |
| Coasting permitted | Whether the room may coast through this window |

Windows must cover the whole day with no gaps and no overlaps. A schedule that
does not is rejected, logged, and ignored — rooms keep working on comfort alone
rather than the integration failing to load.

A window may wrap past midnight. A window whose start equals its end covers the
whole day.

The rate dropdown offers `free`, `cheap`, `off_peak`, `standard`, `shoulder`
and `peak`, and accepts anything else you type. A label you add is offered for
every window afterwards.

Rate is a label, not a price. **The controller does no arithmetic on cost.**
With fixed known windows a rule-based schedule is sufficient and auditable; an
optimiser earns its complexity only with genuinely dynamic prices.

## Constraints

Constraints are the important part. They are **absolute rules, not price
hints**, and are never traded against comfort or price at runtime.

| Constraint | Acted on by | Meaning |
|---|---|---|
| `precool_opportunity` | This controller | Precool may run in this window |
| `no_grid_import` | This controller | Operate against stored energy, not price |
| `grid_charge_battery` | Whoever owns the battery | Not this integration |

**An unrecognised constraint is reported, not dropped.** It raises a repair
issue naming the constraint, so a constraint intended for another system is
visible rather than silently ignored. Adding a constraint for something else to
consume requires no code change here.

## Costs

**Import price** is per window, in cents per kWh, and optional. The controller's
decisions never depend on it: windows and constraints drive behaviour, and the
price is carried so the demand forecast can be costed and so the current price
is visible.

**Feed-in** is one flat rate all day by default, which is what most plans have.
Leave the times at 00:00–00:00 and enter the rate. If yours varies, add one
entry per period with real times — a partial-day entry replaces the flat rate
and turns it into a schedule.

**Daily supply charge** is one figure for the whole house, in cents per day.

All three appear as sensors on the coordinator device, so what you entered is
visible rather than buried in a form you have to reopen to check. So does
**Projected cost**: the demand forecast priced per window, in dollars.

## Coasting

Set `coasting permitted` to false on windows where coasting is the wrong call
even if the thermal model says the room would hold.

The obvious case is a cheap overnight window where a battery is charging: the
energy is cheap and the battery should arrive at the end of the window full, so
there is nothing to protect by coasting and comfort should simply be held.

## When a constraint cannot be honoured

The controller does not override a constraint and does not weigh it against
comfort. If it projects that a constraint and the comfort band cannot both hold,
it says so, with the projection and the reason, and holds the constraint.

You decide what to do about it. That is deliberate: a controller that quietly
relaxes your rules when it finds them inconvenient is worse than one that tells
you it is stuck.

## Moving to dynamic pricing

The tariff is an interface, not a fixed schedule. A dynamic provider supplying
forward intervals drops into the same interface. The constraint model does not
change; only the windows do.
