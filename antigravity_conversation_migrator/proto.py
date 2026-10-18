"""
Pure-Python Protobuf Wire Encoder / Decoder
===========================================
Handles wire types 0 (Varint), 2 (Length-delimited), and tag operations without
external dependencies (no protoc or grpc required).
"""

from typing import List, Tuple, Any, Optional

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

def set_cascade_metadata_project_id(raw_blob: bytes, project_id: str, title: Optional[str] = None) -> bytes:
    """
    Sets or replaces the project_id (subfield 18) inside CascadeMetadata (Field 17)
    of a CascadeTrajectorySummary (raw_summary).
    Also strips any accidental top-level Field 18 and ensures Field 15 title is aligned.
    """
    if not raw_blob:
        # Create minimal valid trajectory summary with Field 17 containing subfield 18
        if project_id and project_id != "outside-of-project":
            f17_bytes = encode_field(18, 2, project_id)
            return encode_field(17, 2, f17_bytes)
        return b""

    fields = decode_proto_fields(raw_blob)
    new_top = bytearray()
    found_17 = False

    for fn, wire, val in fields:
        if fn == 18:
            # Strip accidental top-level field 18
            continue
        elif fn == 17 and wire == 2 and isinstance(val, bytes):
            found_17 = True
            subfields = decode_proto_fields(val)
            new_f17 = bytearray()
            found_18_in_17 = False
            for sfn, sw, sv in subfields:
                if sfn == 18:
                    found_18_in_17 = True
                    if project_id and project_id != "outside-of-project":
                        new_f17.extend(encode_field(18, 2, project_id))
                else:
                    new_f17.extend(encode_field(sfn, sw, sv))
            if not found_18_in_17 and project_id and project_id != "outside-of-project":
                new_f17.extend(encode_field(18, 2, project_id))
            new_top.extend(encode_field(17, 2, bytes(new_f17)))
        elif fn == 15 and wire == 2 and isinstance(val, bytes) and title:
            # Check if Field 15 has Subfield 1 (title); update if present or create
            sub_15 = decode_proto_fields(val)
            new_15 = bytearray()
            found_t = False
            for sfn, sw, sv in sub_15:
                if sfn == 1:
                    found_t = True
                    new_15.extend(encode_field(1, 2, title))
                else:
                    new_15.extend(encode_field(sfn, sw, sv))
            if not found_t:
                new_15.extend(encode_field(1, 2, title))
            new_top.extend(encode_field(15, 2, bytes(new_15)))
        else:
            new_top.extend(encode_field(fn, wire, val))

    if not found_17 and project_id and project_id != "outside-of-project":
        # Field 17 was absent: create it with subfield 18 = project_id
        f17_bytes = encode_field(18, 2, project_id)
        new_top.extend(encode_field(17, 2, f17_bytes))

    return bytes(new_top)

def get_cascade_metadata_project_id(raw_blob: bytes) -> Optional[str]:
    """
    Extracts the project_id (subfield 18) from CascadeMetadata (Field 17).
    """
    if not raw_blob:
        return None
    try:
        fields = decode_proto_fields(raw_blob)
        for fn, wire, val in fields:
            if fn == 17 and wire == 2 and isinstance(val, bytes):
                subfields = decode_proto_fields(val)
                for sfn, sw, sv in subfields:
                    if sfn == 18 and isinstance(sv, bytes):
                        return sv.decode("utf-8", errors="ignore")
                    elif sfn == 18 and isinstance(sv, str):
                        return sv
    except Exception:
        pass
    return None

