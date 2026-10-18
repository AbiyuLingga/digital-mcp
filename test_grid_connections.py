import tempfile
import unittest
from pathlib import Path

from digital_mcp_server import ToolError, _pin, build_circuit


class GridConnectionsTest(unittest.TestCase):
    def test_rejects_off_grid_elements_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "circuit.dig"
            for x, y in ((1250, 140), (1080, 130), (20.5, 40)):
                with self.subTest(x=x, y=y):
                    design = {"elements": [{"id": "gate", "type": "NAnd", "x": x, "y": y}]}
                    with self.assertRaisesRegex(ToolError, "20-unit grid"):
                        build_circuit(design, output)
                    self.assertFalse(output.exists())

    def test_accepts_grid_aligned_elements(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "circuit.dig"
            build_circuit({"elements": [{"id": "gate", "type": "NAnd", "x": 1260, "y": 140}]}, output)
            self.assertTrue(output.exists())

    def test_inverted_gate_outputs_include_the_bubble(self):
        for kind in ("NAnd", "NOr", "XNOr"):
            with self.subTest(kind=kind):
                self.assertEqual(_pin({"type": kind, "x": 100, "y": 100}, 0, True), (180, 120))


if __name__ == "__main__":
    unittest.main()
