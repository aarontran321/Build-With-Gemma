"""De-spin sizing: DETERMINISTIC PHYSICS, never the model's arithmetic.

Why this module exists
----------------------
Picking which sensor to believe is only half a response. If the vehicle is
tumbling, something has to null the rate, and that means committing
propellant on the basis of a rate estimate the system has just finished
arguing about. Sizing that burn is ordinary rigid-body mechanics with an
exact answer, so it is computed here and the model is never asked for the
number.

The split follows the architecture already in place — the arbiter proposes,
deterministic code disposes:

* Gemma proposes the INTENT: is a de-spin warranted at all, about which
  axis, and how urgent. That is a judgement call over ambiguous evidence,
  which is what it is good at.
* This module computes what that intent COSTS: required angular impulse,
  burn time, fraction of available torque.
* guardrail.py compares the two and clamps. A model that asks for a burn
  longer than the physics needs, or one commanded off a sensor it just
  declared faulty, gets overridden with a stated reason.

Physics (single controlled axis, rigid body, constant torque):

    L     = I * omega                 angular momentum to remove  [kg m^2/s]
    t_max = I * omega / tau_max       burn time at full authority [s]
    frac  = t_required / t_available  fraction of torque needed

THE ASYMMETRY THAT MATTERS
--------------------------
Only the gyro yields a signed, axis-resolved rate. The camera proxy is a
residual-magnitude estimate — it can tell you THAT the vehicle is rotating
and roughly how fast, but not about which axis or in which direction. So
"trust the camera" is a survivable answer for detection and a useless one
for control: you cannot null a vector you only know the length of.

That is not a limitation to paper over. It is the interesting result, and
`despin_feasible()` states it explicitly so the dashboard and the report can
show WHY a correct-looking verdict still cannot fly a manoeuvre.
"""

from typing import Optional, Tuple

from . import config
from .schemas import ControlAction, Evidence

# Sensors that can supply a signed body-axis rate. The camera proxy cannot,
# by construction (see module docstring).
AXIS_CAPABLE_SENSORS = {"gyro", "both"}


def despin_feasible(ev: Evidence, trusted_sensor: str) -> Tuple[bool, Optional[str]]:
    """Can a de-spin legitimately be commanded from this evidence?

    Returns (feasible, reason_if_not). Every rejection is a physical fact
    about the estimate, not a policy preference.
    """
    if trusted_sensor not in AXIS_CAPABLE_SENSORS:
        return False, (
            f"trusted sensor '{trusted_sensor}' provides no signed axis rate "
            f"(the camera proxy is magnitude-only); attitude control has no "
            f"usable rate reference")
    if ev.gyro_status == "railed":
        return False, ("gyro is pinned at its rail: the reported rate is a "
                       "limit, not a measurement")
    if ev.gyro_status == "flatlined":
        return False, "gyro is frozen; its axis rates carry no information"
    if abs(ev.spin_rate_signed) < config.DESPIN_DEADBAND_RAD_S:
        return False, (f"spin {abs(ev.spin_rate_signed):.2f} rad/s is inside the "
                       f"{config.DESPIN_DEADBAND_RAD_S} rad/s deadband")
    if ev.spin_axis_stability < config.DESPIN_MIN_AXIS_STABILITY:
        return False, (f"axis estimate unstable ({ev.spin_axis_stability:.2f} < "
                       f"{config.DESPIN_MIN_AXIS_STABILITY}): tumbling, not a "
                       f"single-axis spin")
    # Altitude, when known, buys a time budget. A burn that cannot finish
    # before the ground arrives is worse than no burn: it spends propellant
    # and leaves the vehicle part-corrected at impact. Unknown altitude is
    # NOT treated as "no time" — it is simply no information, and the other
    # checks stand on their own.
    if ev.seconds_to_ground is not None:
        need = burn_for_rate(ev.spin_rate_signed)["burn_s"]
        if need > ev.seconds_to_ground:
            return False, (f"burn needs {need}s but only "
                           f"{ev.seconds_to_ground}s remain to ground "
                           f"(altitude {ev.altitude_m:.0f} m)")
    return True, None


def burn_for_rate(rate_rad_s: float) -> dict:
    """Exact burn required to null `rate_rad_s`. Pure arithmetic.

    Takes a bare rate rather than Evidence so the same physics can be
    evaluated CONTINUOUSLY against the live body rate, not just once at
    decision time. That is what lets the dashboard show the requirement
    falling to zero as a burn takes effect, instead of leaving a stale
    "4.4 s needed" on screen after the spin is already nulled.
    """
    omega = abs(rate_rad_s)
    impulse = config.SPACECRAFT_INERTIA_KG_M2 * omega
    burn_s = impulse / config.THRUSTER_MAX_TORQUE_NM
    clamped = min(burn_s, config.DESPIN_MAX_BURN_S)
    return {
        "angular_impulse_kg_m2_s": round(impulse, 2),
        "burn_s_full_authority": round(burn_s, 2),
        "burn_s": round(clamped, 2),
        "thrust_fraction": 1.0 if burn_s <= config.DESPIN_MAX_BURN_S
                           else round(config.DESPIN_MAX_BURN_S / burn_s, 3),
        "truncated": burn_s > config.DESPIN_MAX_BURN_S,
        # Inside the deadband there is nothing worth spending propellant on:
        # this is the "0 rad/s, therefore no thrust required" state.
        "settled": omega < config.DESPIN_DEADBAND_RAD_S,
    }


def size_burn(ev: Evidence) -> dict:
    """Exact burn required to null the observed spin."""
    return burn_for_rate(ev.spin_rate_signed)


def recommend(ev: Evidence, trusted_sensor: str) -> ControlAction:
    """The de-spin this evidence actually supports, computed not guessed.

    Fire direction OPPOSES the measured spin: a +z rotation needs -z torque.
    """
    feasible, why_not = despin_feasible(ev, trusted_sensor)
    if not feasible:
        return ControlAction(
            manoeuvre="hold_attitude",
            axis=ev.spin_axis,
            fire_direction="none",
            burn_s=0.0,
            thrust_fraction=0.0,
            measured_rate_rad_s=ev.spin_rate_signed,
            rationale=f"no de-spin commanded: {why_not}",
            feasible=False,
        )
    burn = size_burn(ev)
    return ControlAction(
        manoeuvre="despin",
        axis=ev.spin_axis,
        # sign of the required torque is opposite the sign of the rate
        fire_direction="negative" if ev.spin_rate_signed > 0 else "positive",
        burn_s=burn["burn_s"],
        thrust_fraction=burn["thrust_fraction"],
        measured_rate_rad_s=ev.spin_rate_signed,
        rationale=(
            f"null {abs(ev.spin_rate_signed):.2f} rad/s about {ev.spin_axis}: "
            f"{burn['angular_impulse_kg_m2_s']} kg m^2/s of angular momentum, "
            f"{burn['burn_s']}s at {int(burn['thrust_fraction'] * 100)}% torque"
            + (" (clamped to the single-burn limit; expect a follow-up)"
               if burn["truncated"] else "")),
        feasible=True,
    )
