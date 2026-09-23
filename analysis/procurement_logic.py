import math
from datetime import date, datetime


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _finite_quantity(value, field, nonnegative=False):
    value = float(value)
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError(f'{field} must be finite' + (' and non-negative' if nonnegative else ''))
    return value


def confirmed_incoming_before_horizon(shipments, horizon_end):
    """Sum only confirmed positive receipts due by the planning horizon."""
    horizon_end = _as_date(horizon_end)
    total = 0.0
    for shipment in shipments:
        confirmed = shipment.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise ValueError('confirmed must be a boolean')
        if not confirmed:
            continue
        if _as_date(shipment["arrival_date"]) > horizon_end:
            continue
        total += _finite_quantity(shipment["quantity"], 'shipment quantity', nonnegative=True)
    return _finite_quantity(total, 'incoming total', nonnegative=True)


def forecast_inventory_position(on_hand, reserved=0, incoming=0):
    """Return stock available to cover future demand.

    ``incoming`` must already contain only confirmed receipts arriving no later
    than the end of the calculation horizon.
    """
    position = (_finite_quantity(on_hand, 'on_hand')
        - _finite_quantity(reserved, 'reserved', nonnegative=True)
        + _finite_quantity(incoming, 'incoming', nonnegative=True))
    return _finite_quantity(position, 'inventory_position')


def round_purchase_quantity(
    raw_required,
    pack_multiple=None,
    minimum_order_quantity=None,
):
    """Apply MOQ and pack rounding to a non-negative purchase requirement."""
    raw_required = float(raw_required)
    if not math.isfinite(raw_required):
        raise ValueError("raw_required must be finite")
    if minimum_order_quantity is not None:
        minimum_order_quantity = float(minimum_order_quantity)
        if not math.isfinite(minimum_order_quantity) or minimum_order_quantity < 0:
            raise ValueError("minimum_order_quantity must be finite and non-negative")
    if pack_multiple is not None:
        pack_multiple = float(pack_multiple)
        if not math.isfinite(pack_multiple) or pack_multiple <= 0:
            raise ValueError("pack_multiple must be finite and positive")
    if raw_required <= 0:
        return 0.0

    quantity = max(raw_required, minimum_order_quantity or 0)
    if pack_multiple is not None:
        ratio = quantity / pack_multiple
        if not math.isfinite(ratio):
            raise ValueError("Purchase quantity exceeds the supported numeric range")
        # Floating subtraction can produce e.g. 0.30000000000000004 / 0.1.
        # Correct only machine-precision noise, not a genuine partial pack.
        nearest = round(ratio)
        if abs(ratio - nearest) <= 8 * math.ulp(ratio):
            ratio = nearest
        quantity = math.ceil(ratio) * pack_multiple
    if not math.isfinite(quantity):
        raise ValueError("Purchase quantity exceeds the supported numeric range")

    return quantity


def recommended_purchase_quantity(
    demand_during_lead_time,
    safety_stock,
    on_hand,
    reserved=0,
    incoming=0,
    pack_multiple=None,
    minimum_order_quantity=None,
):
    inventory_position = forecast_inventory_position(
        on_hand=on_hand,
        reserved=reserved,
        incoming=incoming,
    )
    raw_required = max(
        0.0,
        _finite_quantity(demand_during_lead_time, 'demand_during_lead_time', nonnegative=True)
        + _finite_quantity(safety_stock, 'safety_stock', nonnegative=True)
        - inventory_position,
    )
    rounded = round_purchase_quantity(
        raw_required,
        pack_multiple=pack_multiple,
        minimum_order_quantity=minimum_order_quantity,
    )
    return {
        "inventory_position": inventory_position,
        "raw_required": raw_required,
        "recommended_quantity": rounded,
    }
