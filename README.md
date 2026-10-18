# Digital MCP Server

Use an MCP client to build and work with logic circuits in [Digital](https://github.com/hneemann/Digital). The server creates `.dig` files and can inspect, test, simulate, and export circuits without controlling the application window.

## What it does

- Build a circuit from components and wire connections.
- Inspect a `.dig` file and check its components and connections.
- Run circuit tests with Digital's command-line simulator.
- Export circuit diagrams, simulation images, and truth tables.
- Open a circuit in Digital on macOS.

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
| `DIGITAL_JAVA` | Java 21 executable |
| `DIGITAL_APP` | Digital application path, used to open circuits on macOS |
| `DIGITAL_SVG_CONVERTER` | SVG-to-PNG converter path |
| `DIGITAL_MCP_OUTPUT_DIR` | Default directory for generated files |

PNG export needs `rsvg-convert`, `magick`, `convert`, or `inkscape`.

## Main tools

| Tool | Purpose |
| --- | --- |
| `digital_build_circuit` | Create a `.dig` circuit from a design, optionally run tests or export SVG |
| `digital_inspect_circuit` | List circuit components and wires |
| `digital_run_tests` | Run the circuit's embedded tests |
| `digital_export_rendered_image` | Export a circuit diagram as SVG or PNG |
| `digital_export_simulation_image` | Set input states and export the simulated circuit |
| `digital_export_truth_table_image` | Run embedded tests and export their truth table |
| `digital_load_circuit` | Open a circuit in Digital on macOS |

For the full design schema and supported components, call `digital_design_schema` from your MCP client.

## Validate locally

```bash
python3 -m unittest discover -v
python3 -m py_compile digital_mcp_server.py
```
