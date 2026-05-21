# Bambu Studio Agent API

This fork adds a Codex-oriented batch automation bridge:

```bash
build/arm64/BambuStudio/BambuStudio.app/Contents/MacOS/BambuStudio \
  --agent-run request.json \
  --agent-out response.json
```

The app runs `resources/agent/bambu_agent.py` from the bundled Resources folder
and falls back to the repo script when launched from the source tree. Requests
and responses are JSON.

## Inspect a 3MF

```json
{
  "schema_version": 1,
  "request_id": "inspect",
  "method": "project.inspect",
  "params": {
    "input": "/Users/justin/Documents/BambuStudio/BoatSmall.3mf"
  }
}
```

## Partition BoatSmall With Dovetail Connectors

```json
{
  "schema_version": 1,
  "request_id": "boat-dovetail",
  "method": "project.auto_partition_for_printer",
  "params": {
    "input": "/Users/justin/Documents/BambuStudio/BoatSmall.3mf",
    "output": "/Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf",
    "added_subpieces": 5
  }
}
```

The generated file keeps the existing two example cuts, subdivides the largest
middle boat section into six pieces, adds alternating dovetail male connectors
and matching negative socket volumes, assigns one piece per plate, and centers
each piece on its own plate.

## Add a Reassembly Plate

```json
{
  "schema_version": 1,
  "request_id": "boat-reassembly-plate",
  "method": "project.add_reassembly_plate",
  "params": {
    "input": "/Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf",
    "output": "/Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf",
    "source": "/Users/justin/Documents/BambuStudio/BoatSmall.3mf"
  }
}
```

This adds a new plate, copies the existing generated pieces as additional
instances, and uses the original source 3MF transforms to arrange those copied
pieces back into the source boat shape on the new plate. The current
`BoatSmall_agent_dovetail_8plates.3mf` test artifact has nine plates after this
operation: plates 1-8 keep one printable piece each, and plate 9 contains all
eight copied pieces in reassembled form.

## Response Shape

Every response includes:

- `ok`
- `request_id`
- `schema_version`
- `summary`
- `errors`
- `warnings`
- `artifacts`

Printer send/control and live camera endpoints remain guarded work: they should
require an explicit physical-printer authorization flag before any real printer
action is sent.

## Verification Rule

Structural validation is not enough. Bambu Studio can reinterpret otherwise
valid 3MF metadata during load. Before claiming a generated file is ready for
the user, open it in the custom Bambu Studio app and visually verify the plate
count, object placement, orientation, and arrangement.
