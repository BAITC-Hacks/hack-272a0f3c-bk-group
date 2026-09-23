import math
from datetime import date, datetime


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def confirmed_incoming_before_horizon(shipments, horizon_end):
    """Sum only confirmed positive receipts due by the planning horizon."""
    horizon_end = _as_date(horizon_end)
    total = 0.0
    for shipment in shipments:
        if not shipment.get("confirmed", False):
            continue
        if _as_date(shipment["arrival_date"]) > horizon_end:
            continue
        total += max(0.0, float(shipment["quantity"]))
    return total


def forecast_inventory_position(on_hand, reserved=0, incoming=0):
    """Return stock available to cover future demand.

    ``incoming`` must already contain only confirmed receipts arriving no later
    than the end of the calculation horizon.
    """
    return float(on_hand) - float(reserved) + float(incoming)


def round_purchase_quantity(
    raw_required,
    pack_multiple=None,
    minimum_order_quantity=None,
):
    """Apply MOQ and pack rounding to a non-negative purchase requirement."""
    raw_required = float(raw_required)
    if raw_required <= 0:
        return 0.0

    quantity = raw_required
    if minimum_order_quantity is not None:
        minimum_order_quantity = float(minimum_order_quantity)
        if minimum_order_quantity < 0:
            raise ValueError("minimum_order_quantity must be non-negative")
        quantity = max(quantity, minimum_order_quantity)

    if pack_multiple is not None:
        pack_multiple = float(pack_multiple)
        if pack_multiple <= 0:
            raise ValueError("pack_multiple must be positive")
        quantity = math.ceil(quantity / pack_multiple) * pack_multiple

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
        float(demand_during_lead_time)
        + float(safety_stock)
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
