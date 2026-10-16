import unittest
from antigravity_conversation_migrator.proto import (
    encode_varint,
    encode_field,
    decode_proto_fields,
    add_or_replace_field,
)

class TestProto(unittest.TestCase):
    def test_varint_roundtrip(self):
        cases = [0, 1, 127, 128, 300, 16384, 2097151, 268435455]
        for val in cases:
            enc = encode_varint(val)
            fields = decode_proto_fields(b"\x08" + enc) # field 1 wire 0
            self.assertEqual(len(fields), 1)
            self.assertEqual(fields[0][0], 1)
            self.assertEqual(fields[0][1], 0)
            self.assertEqual(fields[0][2], val)

    def test_string_field_roundtrip(self):
        test_str = "Antigravity Project 2.0"
        enc = encode_field(18, 2, test_str)
        fields = decode_proto_fields(enc)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0][0], 18)
        self.assertEqual(fields[0][1], 2)
        self.assertEqual(fields[0][2], test_str.encode('utf-8'))

    def test_add_or_replace_field(self):
        # Initial: Field 1 wire 2 = 'initial'
        initial = encode_field(1, 2, "initial")
        # Add field 18 wire 2 = 'my-uuid-1234'
        updated = add_or_replace_field(initial, 18, 2, "my-uuid-1234")
        fields = dict((fn, val) for fn, wire, val in decode_proto_fields(updated))
        self.assertEqual(fields[1], b"initial")
        self.assertEqual(fields[18], b"my-uuid-1234")

        # Replace field 18
        replaced = add_or_replace_field(updated, 18, 2, "new-uuid-5678")
        fields2 = dict((fn, val) for fn, wire, val in decode_proto_fields(replaced))
        self.assertEqual(fields2[1], b"initial")
        self.assertEqual(fields2[18], b"new-uuid-5678")

if __name__ == "__main__":
    unittest.main()
