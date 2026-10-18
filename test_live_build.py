import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import digital_mcp_server as server


class LiveBuildTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(server, "GENERATED", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.launch = patch.object(server, "_launch_live", lambda directory: None)
        self.launch.start()
        self.addCleanup(self.launch.stop)

    def start(self, name="live.dig"):
        return server.call_tool("digital_live_start", {"file_name": name})

    def append(self, session, elements=None, wires=None):
        return server.call_tool("digital_live_append", {
            "session_id": session["session_id"], "elements": elements or [], "wires": wires or []})

    def test_publishes_two_partial_revisions_before_finish(self):
        session = self.start()
        first = self.append(session, [{"id": "a", "type": "In", "x": 100, "y": 100}])
        self.assertEqual((first["revision"], first["element_count"]), (1, 1))
        self.assertFalse((self.root / "live.dig").exists())
        second = self.append(session, [{"id": "y", "type": "Out", "x": 300, "y": 100}],
                             [{"from": "a", "to": "y"}])
        self.assertEqual((second["revision"], second["element_count"], second["wire_count"]), (2, 2, 1))
        final = server.call_tool("digital_live_finish", {"session_id": session["session_id"]})
        self.assertEqual(final["state"], "complete")
        self.assertEqual(server.inspect_circuit(self.root / "live.dig")["wire_count"], 1)
        with self.assertRaisesRegex(server.ToolError, "complete"):
            self.append(session, [{"id": "b", "type": "In"}])

    def test_invalid_updates_do_not_publish(self):
        session = self.start()
        self.append(session, [{"id": "a", "type": "In", "x": 100, "y": 100}])
        directory = self.root / ".live" / session["session_id"]
        original = (directory / "state.properties").read_bytes()
        bad = [
            ([{"id": "a", "type": "Out"}], []),
            ([{"id": "b", "type": "And", "x": 25}], []),
            ([], [{"from": "a", "to": "missing"}]),
            ([], [{"p1": {"x": 100, "y": 100}, "p2": {"x": 200, "y": 200}}]),
            ([], [{"p1": {"x": 100.5, "y": 100}, "p2": {"x": 200, "y": 100}}]),
        ]
        for elements, wires in bad:
            with self.subTest(elements=elements, wires=wires):
                with self.assertRaises(server.ToolError):
                    self.append(session, elements, wires)
                self.assertEqual((directory / "state.properties").read_bytes(), original)

    def test_stop_and_existing_output_are_preserved(self):
        session = self.start()
        directory = self.root / ".live" / session["session_id"]
        (directory / "stop").touch()
        with self.assertRaisesRegex(server.ToolError, "stopped"):
            self.append(session, [{"id": "a", "type": "In"}])
        with self.assertRaises(server.ToolError):
            server.call_tool("digital_live_finish", {"session_id": session["session_id"]})
        output = self.root / "existing.dig"
        output.write_text("keep me")
        with self.assertRaisesRegex(server.ToolError, "exists"):
            self.start("existing.dig")
        another = self.start("collision.dig")
        (self.root / "collision.dig").write_text("created during generation")
        with self.assertRaisesRegex(server.ToolError, "exists"):
            server.call_tool("digital_live_finish", {"session_id": another["session_id"]})
        self.assertEqual((self.root / "collision.dig").read_text(), "created during generation")

    def test_finish_uses_embedded_tests_and_reports_failure(self):
        session = self.start()
        with patch.object(server, "run_tests", return_value={"passed": False, "output": "bad logic"}) as run:
            result = server.call_tool("digital_live_finish", {
                "session_id": session["session_id"],
                "tests": [{"inputs": {"A": 0}, "outputs": {"Y": 1}}]})
        self.assertFalse(result["tests"]["passed"])
        self.assertEqual(result["state"], "complete")
        run.assert_called_once()
        self.assertEqual(server.inspect_circuit(Path(result["path"]))["elements"][0]["type"], "Testcase")

    def test_session_path_cannot_escape(self):
        with self.assertRaises(server.ToolError):
            server.call_tool("digital_live_append", {"session_id": "../../elsewhere", "elements": []})

    def test_duplicate_overlapping_and_invalid_pin_wires_are_rejected(self):
        session = self.start()
        self.append(session, [{"id": "a", "type": "In", "x": 100, "y": 100},
                              {"id": "y", "type": "Out", "x": 300, "y": 100}],
                    [{"from": "a", "to": "y"}])
        for wire in ({"from": "a", "to": "y"},
                     {"p1": {"x": 140, "y": 100}, "p2": {"x": 200, "y": 100}},
                     {"from": "a", "to": "y", "input": 1}):
            with self.subTest(wire=wire), self.assertRaises(server.ToolError):
                self.append(session, wires=[wire])

    def test_missing_runtime_keeps_draft_and_does_not_create_final_file(self):
        with patch.object(server, "_launch_live", side_effect=server.ToolError("no Java runtime")):
            with self.assertRaisesRegex(server.ToolError, "saved draft"):
                self.start()
        directories = list((self.root / ".live").iterdir())
        self.assertEqual(len(directories), 1)
        self.assertTrue((directories[0] / "revision-000000.dig").exists())
        self.assertTrue((directories[0] / "stop").exists())
        self.assertFalse((self.root / "live.dig").exists())

    def test_closed_or_failed_viewer_does_not_accept_more_work(self):
        for state in ("closed", "error"):
            session = self.start(state + ".dig")
            directory = self.root / ".live" / session["session_id"]
            (directory / "viewer.properties").write_text(f"state={state}\nmessage=test\n")
            with self.assertRaisesRegex(server.ToolError, state):
                self.append(session, [{"id": "a", "type": "In"}])

    def test_invalid_append_argument_shapes_return_tool_errors(self):
        session = self.start()
        for fields in ({}, {"elements": {}}, {"elements": [None]}, {"wires": [None]}):
            with self.subTest(fields=fields), self.assertRaises(server.ToolError):
                server.call_tool("digital_live_append", {"session_id": session["session_id"], **fields})


@unittest.skipUnless(os.environ.get("DIGITAL_LIVE_GUI_TEST") == "1", "opt-in real Digital desktop test")
class LiveDesktopTest(unittest.TestCase):
    def test_native_window_observes_partial_revisions_then_final_tests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "GENERATED", Path(directory)), patch.dict(os.environ):
            os.environ["JAVA_TOOL_OPTIONS"] = (os.environ.get("JAVA_TOOL_OPTIONS", "")
                                               + f" -Djava.util.prefs.userRoot={directory}/prefs").strip()
            children = []
            original = subprocess.Popen

            def launch(*args, **kwargs):
                process = original(*args, **kwargs)
                children.append(process)
                return process

            try:
                with patch.object(server.subprocess, "Popen", side_effect=launch):
                    session = server.call_tool("digital_live_start", {"file_name": "not.dig"})
                folder = Path(directory) / ".live" / session["session_id"]

                def observed(revision, state="building"):
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        view = server._read_properties(folder / "viewer.properties")
                        if view.get("state") == "error":
                            self.fail(view.get("message", "viewer error"))
                        if int(view.get("revision", -1)) == revision and view.get("state") == state:
                            return view
                        time.sleep(0.05)
                    self.fail("Viewer did not observe revision: " + (folder / "viewer.log").read_text())

                server.call_tool("digital_live_append", {"session_id": session["session_id"], "elements": [
                    {"id": "a", "type": "In", "label": "A", "x": 100, "y": 100}]})
                self.assertEqual(observed(1)["elements"], "1")
                self.assertFalse((Path(directory) / "not.dig").exists())
                server.call_tool("digital_live_append", {"session_id": session["session_id"], "elements": [
                    {"id": "n", "type": "Not", "x": 240, "y": 100},
                    {"id": "y", "type": "Out", "label": "Y", "x": 400, "y": 100}], "wires": [
                    {"from": "a", "to": "n"}, {"from": "n", "to": "y"}]})
                view = observed(2)
                self.assertEqual((view["elements"], view["wires"]), ("3", "2"))
                final = server.call_tool("digital_live_finish", {"session_id": session["session_id"], "tests": [
                    {"inputs": {"A": 0}, "outputs": {"Y": 1}},
                    {"inputs": {"A": 1}, "outputs": {"Y": 0}}]})
                self.assertTrue(final["tests"]["passed"], final["tests"])
                self.assertIn("PASS", observed(3, "complete")["message"])
                self.assertTrue(Path(final["path"]).exists())
            finally:
                for process in children:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
