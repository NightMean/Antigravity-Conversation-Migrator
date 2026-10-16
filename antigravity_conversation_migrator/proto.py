"""
Pure-Python Protobuf Wire Encoder / Decoder
===========================================
Handles wire types 0 (Varint), 2 (Length-delimited), and tag operations without
external dependencies (no protoc or grpc required).
"""

from typing import List, Tuple, Any

def decode_proto_fields(buf: bytes) -> List[Tuple[int, int, Any]]:
    """
    Decodes serialized protobuf wire format into a list of (field_number, wire_type, value).
    Supports wire type 0 (varint) and wire type 2 (length-delimited bytes).
    """
    offset = 0
    fields = []
    while offset < len(buf):
        shift = 0
        tag = 0
        while True:
            if offset >= len(buf):
                break
            byte = buf[offset]
            offset += 1
            tag |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        if offset > len(buf):
            break
        wire = tag & 7
        fn = tag >> 3
        if wire == 0:
            shift = 0
            val = 0
            while True:
                if offset >= len(buf):
                    break
                byte = buf[offset]
                offset += 1
                val |= (byte & 0x7F) << shift
                if not (byte & 0x80):
                    break
                shift += 7
            fields.append((fn, wire, val))
        elif wire == 2:
            shift = 0
            l = 0
            while True:
                if offset >= len(buf):
                    break
                byte = buf[offset]
                offset += 1
                l |= (byte & 0x7F) << shift
                if not (byte & 0x80):
                    break
                shift += 7
            val = buf[offset:offset + l]
            offset += l
            fields.append((fn, wire, val))
        else:
            # Wire types 1, 3, 4, 5 encountered, break to prevent corrupted parsing
            break
    return fields

def encode_varint(val: int) -> bytes:
    """Encodes an integer into standard protobuf varint bytes."""
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

def encode_field(fn: int, wire: int, data: Any) -> bytes:
    """Encodes a single protobuf field (tag + payload)."""
    tag = (fn << 3) | wire
    tag_bytes = encode_varint(tag)
    if wire == 0:
        return tag_bytes + encode_varint(data)
    elif wire == 2:
        if isinstance(data, str):
            data = data.encode('utf-8')
        return tag_bytes + encode_varint(len(data)) + data
    raise ValueError(f"Unsupported wire type {wire}")

def add_or_replace_field(raw_blob: bytes, fn: int, wire: int, data: Any) -> bytes:
    """
    Inserts or replaces a specific field number in a protobuf payload.
    Preserves all surrounding fields intact.
    """
    new_field_bytes = encode_field(fn, wire, data)
    tag_bytes = encode_varint((fn << 3) | wire)
    
    idx = raw_blob.find(tag_bytes)
    if idx != -1 and wire == 2:
        offset = idx + len(tag_bytes)
        l = 0
        shift = 0
        l_bytes_len = 0
        while offset < len(raw_blob):
            byte = raw_blob[offset]
            offset += 1
            l_bytes_len += 1
            l |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        total_old_len = len(tag_bytes) + l_bytes_len + l
        return raw_blob[:idx] + new_field_bytes + raw_blob[idx + total_old_len:]
    elif idx != -1 and wire == 0:
        offset = idx + len(tag_bytes)
        while offset < len(raw_blob) and (raw_blob[offset] & 0x80):
            offset += 1
        offset += 1
        return raw_blob[:idx] + new_field_bytes + raw_blob[offset:]
    else:
        return raw_blob + new_field_bytes
