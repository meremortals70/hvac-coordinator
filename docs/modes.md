# Modes

Every room is in exactly one mode. The mode determines which comfort band
applies and whether the room may actuate at all.

## Precedence

Evaluated top to bottom. The first match wins.

| Mode | Entered when | Behaviour |
|---|---|---|
| `LOCKOUT` | The lockout box is ticked for this room | Never actuates. Beats everything |
| `PRECONDITION` | A heading-home request is active | Drives to the occupied band, ignoring presence |
| `PRECOOL` | Occupied, a precool window is declared, and demand is forecast ahead | Drives to the low bound to bank thermal mass |
| `COAST` | The thermal model predicts the band holds unaided, and the window permits coasting | No compressor |
| `SLEEP` | The sleep schedule is on | Sleep band |
| `OCCUPIED` | Presence detected, or presence unknown | Occupied band |
| `UNOCCUPIED` | No presence | **Off.** Not a wider band |

## The ones that surprise people

**An unoccupied room is off.** Completely. It is not held to a wider envelope,
and it does not run at reduced effort. The only thing that brings it back on is
a heading-home request. If you want an empty room conditioned, ask for it.

**Unknown presence holds occupied.** A presence sensor that has died, dropped
off the network or never reported reads as unknown, not absent. Treating unknown
as absent would let a flat battery in a sensor turn off the air conditioning in
an occupied house. The trace says `presence unknown, holding occupied` when this
happens, so it is visible rather than silent.

**The sleep schedule still applies when presence is unknown.** A dead presence
sensor at 2am should not put the room on the day band.

**Precondition beats presence, deliberately.** That is the entire point of it —
it conditions a room before anyone is there.

**Precool will not run in an unoccupied room.** Free energy is not a reason to
cool a room nobody is in. Precool is evaluated after occupancy for exactly this
reason.

**Precool stops at the low bound.** It drives to the bottom of the band and then
stops, rather than continuing to run because free energy is available.

**Coast carries the band of the mode it displaced.** Coast has no band of its
own. A coasting room that was occupied is still held to the occupied band, which
is what the model is predicting will hold. Without that, there would be nothing
to compare against when deciding to leave coast.

## Modes that cannot currently be entered

**`COAST`** requires the thermal model, which is not built. `predicted_to_hold`
is always `None`, and the trace says
`coast: thermal model has not converged for this room`.

**`PRECOOL`** requires the demand forecast, which is not built.
`forecast_demand_ahead` is always `False`.

Both are wired end to end and unit tested. They are waiting on inputs, not on
logic. See [Known limitations](known-limitations.md).

## Lockout

Ticking the lockout box means the room is never actuated, whatever else is
true. A further step asks why, offering a dropdown of built-in reasons plus any
you have typed before; a new reason is stored for the whole installation.

The reason appears in the decision trace, so a room that is doing nothing
always says why.

This exists for rooms under renovation, rooms whose unit is physically
disconnected, and rooms you want configured but not yet live.
