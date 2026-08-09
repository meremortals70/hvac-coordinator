# Tariff

## Windows

A tariff is a list of windows. Each window carries:

| Field | Meaning |
|---|---|
| Start | Inclusive |
| End | Exclusive |
| Rate | A label — `cheap`, `free`, `peak`, whatever you use |
| Constraints | Absolute rules that apply during this window |
| Coasting permitted | Whether the room may coast through this window |

Windows must cover the whole day with no gaps and no overlaps. A schedule that
does not is rejected, logged, and ignored — rooms keep working on comfort alone
rather than the integration failing to load.

A window may wrap past midnight. A window whose start equals its end covers the
whole day.

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
