"""Proof that Fix 4 is needed: assert is stripped with -O, should be ValueError."""
import uuid
import pytest
from app.services.receipts import write_receipt

async def test_fix4_invalid_action_type_raises_value_error(db_session):
    with pytest.raises(ValueError) as exc:
        await write_receipt(
            db_session,
            action_type="totally_invalid",
            entity_id=uuid.uuid4(),
            entity_type="test",
        )
    assert "Unknown action_type" in str(exc.value)
    assert "VALID_ACTION_TYPES" in str(exc.value)

async def test_fix4_valid_action_type_does_not_raise(db_session):
    # Sanity: valid type still works
    import uuid as _u
    row = await write_receipt(
        db_session,
        action_type="event_created",
        entity_id=_u.uuid4(),
        entity_type="event",
        event_id=_u.uuid4(),
    )
    assert row.action_type == "event_created"
