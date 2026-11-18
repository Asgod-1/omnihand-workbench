import unittest
from types import SimpleNamespace
from omnihand_usb.raw_bridge import EXPECTED_COUNTS
from omnihand_usb.raw_tactile import DirectRawReader,decode_fragments

def frame_sequence(values):
    data=bytes(values)
    return {i:bytes((5,i))+data[i*62:(i+1)*62]+(b'\xaa\xbb' if i==4 else b'') for i in range(5)}

class RawFragmentTests(unittest.TestCase):
    def test_seven_regions_keep_each_value_and_exclude_can_padding(self):
        values=[i%256 for i in range(268)]
        result=decode_fragments(frame_sequence(values))
        self.assertEqual({k:len(v['values']) for k,v in result.items()},EXPECTED_COUNTS)
        self.assertEqual([x for r in result.values() for x in r['values']],values)

    def test_missing_mismatched_and_short_fragments_are_rejected(self):
        for kind in ('missing','mismatched','short'):
            frames=frame_sequence([0]*268)
            if kind=='missing':del frames[2]
            elif kind=='mismatched':frames[2]=bytes((5,3))+bytes(62)
            else:frames[2]=bytes((5,2))+bytes(61)
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):decode_fragments(frames)

    def test_direct_read_accepts_only_fresh_complete_packets_and_uses_explicit_get(self):
        holder={};calls=[]
        frames=frame_sequence([i%256 for i in range(268)])
        def send(channel,cid,length,payload,**flags):
            calls.append((cid,length,payload))
            reader=holder['reader']
            reader.receive(0x60102,frames[0])
            reader.receive(cid,frames[3])  # Late tail before a fresh first frame.
            for f in frames.values():reader.receive(cid,f)
            return True
        reader=DirectRawReader(SimpleNamespace(send_can=send),0,1,timeout=.01)
        holder['reader']=reader
        result=reader.read()
        self.assertEqual(result['status'],'ready')
        self.assertEqual(calls,[(0x60101,1,b'\x00')])
        self.assertEqual(sum(len(v['values']) for v in result['regions'].values()),268)

if __name__=='__main__':unittest.main()
