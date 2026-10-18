# Digital MCP Server

Use an MCP client to build and work with logic circuits in [Digital](https://github.com/hneemann/Digital). The server creates `.dig` files and can inspect, test, simulate, and export circuits without controlling the application window.

## What it does

- Build a circuit from components and wire connections.
- Inspect a `.dig` file and check its components and connections.
- Run circuit tests with Digital's command-line simulator.
- Export circuit diagrams, simulation images, and truth tables.
- Open a circuit in Digital on macOS.
- Watch gates and wires appear while the AI is still building a new circuit.

## Setup

Start the server with Python:

```bash
python3 /path/to/digital-mcp-server/digital_mcp_server.py
```

Add it to your MCP client configuration. Set the paths for your Digital installation as needed:

```json
{
  "mcpServers": {
    "digital": {
      "command": "python3",
      "args": ["/path/to/digital-mcp-server/digital_mcp_server.py"],
      "env": {
        "DIGITAL_JAR": "/path/to/Digital/source/target/Digital.jar",
        "DIGITAL_APP": "/path/to/Digital/modern/dist/Digital.app"
      }
    }
  }
}
```

`DIGITAL_JAR` is recommended when the server and Digital are installed in different directories. Other optional settings:

| Variable | Purpose |
| --- | --- |
| `DIGITAL_PROJECT_ROOT` | Digital source project directory |
| `DIGITAL_JAVA` | Java executable; Live Build needs a desktop JDK 17+ |
| `DIGITAL_APP` | Digital application path, used to open circuits on macOS |
| `DIGITAL_SVG_CONVERTER` | SVG-to-PNG converter path |
| `DIGITAL_MCP_OUTPUT_DIR` | Default directory for generated files |

PNG export needs `rsvg-convert`, `magick`, `convert`, or `inkscape`.

## Live Build

Live Build shows a new circuit growing in a Digital window as your AI agent sends updates. Ask your agent:

> Build a D latch in live mode. Open the live window first, then add gates and wires in small batches as you design it.

The agent uses three tools:

1. `digital_live_start`: open an empty circuit and return a session ID.
2. `digital_live_append`: send a small group of components and wires using that session ID. Repeat while designing.
3. `digital_live_finish`: save the final `.dig`, optionally run embedded tests, and enable editing in the same window.

The live window allows zoom and pan during construction. Editing and simulation are disabled until completion. Between updates, it shows **Waiting for AI…**; it cannot display changes the agent has not sent yet.

Live Build requires a **desktop JDK 17 or newer**, including its compiler, and a local `Digital.jar`. Set `DIGITAL_JAR` and, if needed, `DIGITAL_JAVA` to the JDK's `java` executable. The Java companion is included; you do not need to replace your Digital installation. Restart your MCP connection after upgrading to discover the new tools.

Only new circuits are supported. Each append adds components or wires; it cannot move or delete earlier components. Use unique component IDs, coordinates on the 20-unit grid, and horizontal/vertical wire segments with explicit bends. Existing output files are never overwritten.

If you need to stop, choose **Stop and keep draft** in the live window. Your agent's later updates are rejected, and you can edit the saved draft. Session drafts also remain under `generated/.live/` (or your configured output directory) if the agent disconnects.

The Linux desktop integration is checked against Digital v0.30. Windows and macOS use the same Java launcher, but are not yet verified on those platforms.

## Main tools

| Tool | Purpose |
| --- | --- |
| `digital_live_start` | Start watching a new circuit before the complete design is ready |
| `digital_live_append` | Add the next group of gates and wires to the live circuit |
| `digital_live_finish` | Save the result, report optional tests, and enable editing |
| `digital_build_circuit` | Create a `.dig` circuit from a design, optionally run tests or export SVG |
| `digital_inspect_circuit` | List circuit components and wires |
| `digital_run_tests` | Run the circuit's embedded tests |
| `digital_export_rendered_image` | Export a circuit diagram as SVG or PNG |
| `digital_export_simulation_image` | Set input states and export the simulated circuit |
| `digital_export_truth_table_image` | Run embedded tests and export their truth table |
| `digital_load_circuit` | Open a circuit in Digital on macOS |

For the full design schema and supported components, call `digital_design_schema` from your MCP client.

## Circuit reference

Circuit logic should follow Brown and Vranesic's *Fundamentals of Digital Logic with VHDL Design*, third edition (2009), where applicable. See [the verified circuit references](CIRCUIT_REFERENCES.md) for D latch, master-slave D, T, and JK flip-flops. `digital_design_schema` also returns these references, including clock behavior and figure/page numbers. Digital layouts may be adapted; the PDF is not included.

## Validate locally

```bash
python3 -m unittest discover -v
python3 -m py_compile digital_mcp_server.py
```
