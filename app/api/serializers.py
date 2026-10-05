"""
Opaque ID Marshmallow and dict serializers for the TMA API (TDR v2 §6, Milestone P7).
"""

from marshmallow import fields, ValidationError
from app.services import id_codec


def encode_id(int_id, kind=None):
    """Encodes an integer ID to an opaque hashid token."""
    if int_id is None:
        return None
    # print(f"[DEBUG] api id encode kind={kind} value={int_id}")
    return id_codec.encode(int_id)


def decode_id(val, kind=None):
    """
    Decodes an opaque hashid token to an integer ID.
    Rejects raw integers and invalid tokens by raising ValueError.
    """
    if val is None:
        return None
    if isinstance(val, int):
        raise ValueError(f"Integer IDs are not accepted for {kind or 'field'}; opaque string tokens required.")
    decoded = id_codec.decode(str(val))
    if decoded is None:
        raise ValueError(f"Invalid opaque identifier for {kind or 'field'}.")
    return decoded


class HashidField(fields.Field):
    """
    Marshmallow custom field that encodes integer PKs to opaque string tokens on dump,
    and decodes opaque tokens into integer PKs on load.
    Rejects raw integers to enforce opaque tokens at the API boundary.
    """

    def _serialize(self, value, attr, obj, **kwargs):
        if value is None:
            return None
        # print(f"[DEBUG] api id encode kind={attr} value={value}")
        return id_codec.encode(value)

    def _deserialize(self, value, attr, data, **kwargs):
        if value is None:
            return None
        if isinstance(value, int):
            raise ValidationError("Integer IDs are not accepted; opaque string tokens required.")
        decoded = id_codec.decode(str(value))
        if decoded is None:
            raise ValidationError("Invalid opaque identifier.")
        return decoded
