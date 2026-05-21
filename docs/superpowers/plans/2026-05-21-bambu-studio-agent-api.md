# Bambu Studio Agent API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan.

## Goal

Build a custom macOS Bambu Studio fork that gives Codex and other agents a stable automation surface for:

- Loading, inspecting, editing, slicing, and saving Bambu 3MF projects.
- Importing models, changing object/part/volume structure, assigning plates, and editing print, filament, printer, object, and part settings.
- Cutting oversized models into printable pieces with reassembly geometry, including dovetail-style joints.
- Sending jobs to the user's Bambu Lab H2D and controlling active jobs.
- Capturing printer camera stills or stream frames, combining them with live printer telemetry, and pausing/canceling jobs when configured monitor rules say to intervene.

The implementation should reuse Bambu Studio's native data structures and printer integrations. Do not automate the GUI as the primary interface.

## Current Codebase Findings

- `src/BambuStudio.hpp` owns the existing `CLI` class and stores input files, actions, transforms, loaded `Model`s, and export helpers.
- `src/BambuStudio.cpp` starts the GUI whenever no CLI action is present, so an agent entrypoint can be added before `GUI_Run` without changing normal app behavior.
- `src/libslic3r/PrintConfig.cpp` exposes CLI actions for slicing, 3MF export, slicedata export/import, PNG export, model info, settings export, and basic transforms.
- Cut and split CLI transforms are present only as commented-out definitions, and the old cut execution path in `src/BambuStudio.cpp` is not a usable advanced-cut API.
- 3MF loading/saving already flows through `Model::read_from_file`, `PlateData`, `DynamicPrintConfig`, and `store_bbs_3mf`. Use these rather than direct ZIP/XML editing.
- Advanced cut functionality exists in GUI code under `src/slic3r/GUI/Gizmos/GLGizmoAdvancedCut.*`, while reusable mesh operations live in `src/libslic3r/Model.*` and `src/libslic3r/CutUtils.*`.
- Current connector enums support `Plug`, `Dowel`, `Snap`, and `Thread`. There is no existing dovetail connector type.
- Job sending is implemented in `src/slic3r/GUI/Jobs/PrintJob.*` through `NetworkAgent` functions such as `start_print`, `start_local_print_with_record`, `start_local_print`, and `start_sdcard_print`.
- Printer pause, resume, stop, object-skip, and camera commands already exist on `MachineObject` in `src/slic3r/GUI/DeviceManager.*`.
- Camera liveview and preview still-image paths already exist in `src/slic3r/GUI/MediaPlayCtrl.cpp`, including LAN/RTSP/TUTK URL selection and `DownloadMemFile("mem:/26")` preview capture.
- Full printer networking depends on the optional non-free Bambu networking plugin loaded from `data_dir()/plugins/libbambu_networking.dylib` on macOS.

## Target Architecture

Add one shared automation layer and expose it two ways:

- Headless batch CLI: `BambuStudio --agent-run request.json --agent-out response.json`
- Long-running local control server: `BambuStudio --agent-server 127.0.0.1:35988 --agent-token-file <path>`

The batch CLI handles deterministic 3MF/model transforms, slicing, validation, and export. The local server handles operations that benefit from the already-initialized GUI app context, selected printer state, authenticated networking plugin, live telemetry, and camera access.

New code should be split by responsibility:

- `src/slic3r/Automation/AgentTypes.hpp`
- `src/slic3r/Automation/AgentTypes.cpp`
- `src/slic3r/Automation/AgentJson.hpp`
- `src/slic3r/Automation/AgentJson.cpp`
- `src/slic3r/Automation/AgentProjectSession.hpp`
- `src/slic3r/Automation/AgentProjectSession.cpp`
- `src/slic3r/Automation/AgentCutPlanner.hpp`
- `src/slic3r/Automation/AgentCutPlanner.cpp`
- `src/slic3r/Automation/AgentDevice.hpp`
- `src/slic3r/Automation/AgentDevice.cpp`
- `src/slic3r/Automation/AgentCamera.hpp`
- `src/slic3r/Automation/AgentCamera.cpp`
- `src/slic3r/Automation/AgentServer.hpp`
- `src/slic3r/Automation/AgentServer.cpp`
- `src/slic3r/Automation/AgentMain.hpp`
- `src/slic3r/Automation/AgentMain.cpp`

Add these files to the existing `libslic3r_gui` target in `src/slic3r/CMakeLists.txt` when they depend on `wxGetApp`, `NetworkAgent`, `MachineObject`, `PrintJob`, or GUI-side project state. Put purely geometric or model serialization helpers in `libslic3r` through `src/libslic3r/CMakeLists.txt` when they do not need wxWidgets or GUI state.

## JSON Command Contract

Use request/response JSON files for batch mode and newline-delimited JSON-RPC for server mode. Every response must include:

- `ok`: boolean
- `request_id`: copied from request
- `errors`: array of structured errors
- `warnings`: array of structured warnings
- `artifacts`: array of generated file paths
- `summary`: compact human-readable text

Initial methods:

- `project.inspect`
- `project.save`
- `project.import_model`
- `project.delete_object`
- `project.set_transform`
- `project.set_plate`
- `project.set_volume_type`
- `project.set_config`
- `project.arrange`
- `project.orient`
- `project.slice`
- `project.export_png`
- `project.cut`
- `project.auto_partition_for_printer`
- `device.list`
- `device.status`
- `device.send_plate`
- `device.pause`
- `device.resume`
- `device.cancel`
- `device.skip_objects`
- `camera.snapshot`
- `camera.stream_frame`
- `monitor.start`
- `monitor.stop`

Keep schemas versioned with `schema_version: 1` so incompatible changes can be made deliberately.

## Implementation Tasks

### 1. Reproducible macOS build baseline

- [ ] Add `script/build_and_run.sh` that wraps `./BuildMac.sh -a "$(uname -m)" -x -s -c RelWithDebInfo` and runs the built `.app` or CLI binary from `build/<arch>`.
- [ ] Add `.codex/environments/environment.toml` with the local build command and expected build artifacts.
- [ ] Verify dependencies with `brew list cmake git gettext nasm yasm x264`.
- [ ] Build dependencies once with `./BuildMac.sh -a "$(uname -m)" -x -d`.
- [ ] Build the app with `./BuildMac.sh -a "$(uname -m)" -x -s -c RelWithDebInfo`.
- [ ] Record whether `build/<arch>/BambuStudio/RelWithDebInfo/BambuStudio.app/Contents/Resources/plugins/libbambu_networking.dylib` is present.

Acceptance:

- A clean checkout builds on this Mac.
- The GUI still launches.
- Running the built binary with `--help` still prints the existing CLI help.
- The networking plugin presence check is documented because printer send/control will not be complete without it.

### 2. Agent batch entrypoint

- [ ] Extend `CLI` in `src/BambuStudio.hpp` with `m_agent_request_path`, `m_agent_response_path`, and `m_agent_server_endpoint`.
- [ ] Add CLI options in `src/libslic3r/PrintConfig.cpp`: `agent_run`, `agent_out`, `agent_server`, and `agent_token_file`.
- [ ] In `CLI::setup`, capture those options before regular action dispatch.
- [ ] In `CLI::run`, after environment initialization and before `start_gui`, call `Slic3r::Automation::run_agent_request(...)` when `agent_run` is present.
- [ ] Return process exit code `0` only when the response has `ok: true`.
- [ ] Add `project.inspect` as the first command using `Model::read_from_file` and the same load strategy used by the current CLI for 3MF input.

Acceptance:

```bash
build/arm64/src/bambu-studio --agent-run /tmp/inspect.json --agent-out /tmp/inspect.out.json
```

returns object names, volumes, instances, bounding boxes, plates, loaded printer profile, filament profiles, process profile, and 3MF version metadata for a known project.

### 3. Project edit and save API

- [ ] Implement `AgentProjectSession` around `Model`, `PlateDataPtrs`, `DynamicPrintConfig`, and project presets.
- [ ] Implement `project.save` through existing `store_bbs_3mf` options equivalent to `CLI::export_project`.
- [ ] Implement `project.set_config` for global print settings, filament-index settings, printer settings, object settings, and volume settings using `DynamicPrintConfig` APIs.
- [ ] Implement `project.import_model`, `project.delete_object`, `project.set_transform`, and `project.set_plate`.
- [ ] Implement `project.set_volume_type` for `MODEL_PART`, `NEGATIVE_VOLUME`, `PARAMETER_MODIFIER`, `SUPPORT_BLOCKER`, and `SUPPORT_ENFORCER`.
- [ ] Preserve unknown 3MF metadata and Bambu project plate data unless a command explicitly changes it.

Acceptance:

- Load a 3MF, change one process setting, add one STL as a new object, convert one volume to `NEGATIVE_VOLUME`, save, reload, and confirm all changes through `project.inspect`.
- The saved 3MF opens in the GUI without repair prompts.

### 4. Plate, slice, and thumbnail API

- [ ] Wrap existing arrange/orient behavior currently reachable through CLI transforms.
- [ ] Implement `project.slice` for one plate or all plates using the same slicing path used by current CLI actions.
- [ ] Implement `project.export_png` using the existing plate PNG path.
- [ ] Return slicing warnings, estimated filament/time, skipped objects, plate bounding boxes, and generated artifact paths.

Acceptance:

- A known multi-plate 3MF can be sliced plate-by-plate.
- Agent output includes enough metadata for Codex to decide which plate/job to send.

### 5. Headless cut backend

- [ ] Create `AgentCutPlan` with object selector, instance selector, plane points, cut mode, keep/flip/place flags, connector list, and output placement strategy.
- [ ] Move non-UI connector-plan conversion from `GLGizmoAdvancedCut` into `AgentCutPlanner`.
- [ ] Execute planar and tongue-and-groove cuts through `ModelObject::cut` and `Cut::perform_with_plane`.
- [ ] Preserve current local `Thread` connector behavior while extracting the backend. Do not remove existing thread connector code unless replacing it with equivalent tested logic.
- [ ] Add mesh validation output: part count, per-part bounding box, volume count, connector count, non-manifold/naked-edge indicators when available, and max dimensions.

Acceptance:

- A JSON cut request can split a selected object without opening the cut gizmo.
- The resulting project reloads and each generated part can be arranged and sliced.

### 6. Dovetail connector geometry

- [ ] Add `Dovetail` to `CutConnectorType` in `src/libslic3r/Model.hpp`.
- [ ] Add serialization, equality, ordering, and UI-safe string conversion for the new connector type.
- [ ] Implement a trapezoid-prism mesh generator in `src/libslic3r/CutUtils.*` or a small geometry helper under `src/libslic3r`.
- [ ] Extend connector processing so each dovetail creates a matching positive key on one side and negative clearance pocket on the mating side.
- [ ] Add parameters: width, depth, taper angle, length, radial/linear tolerance, lead-in chamfer, and alternating male/female side.
- [ ] Add collision checks against cut-plane bounds and neighboring connectors.
- [ ] Add `project.auto_partition_for_printer` that recursively chooses cut planes for an H2D build volume, places dovetails on each shared boundary, and emits per-piece plates.

Acceptance:

- A model larger than the selected H2D build volume is split into pieces that each fit the configured printable volume.
- Adjacent pieces have complementary dovetail geometry with configured clearance.
- All generated pieces slice successfully.

### 7. Device control API

- [ ] Implement `AgentDevice` as a thin wrapper over `DeviceManager`, `MachineObject`, `NetworkAgent`, and `PrintJob`.
- [ ] Implement `device.list` using the same machine catalog used by the GUI.
- [ ] Implement `device.status` from the selected `MachineObject` fields, including print state, job id, progress, bed/nozzle temps, AMS/material state, storage capabilities, camera capability, and liveview modes.
- [ ] Implement `device.send_plate` by creating the same temporary 3MF/config artifacts and `PrintJob` inputs used by `SelectMachineDialog::on_send_print`.
- [ ] Implement `device.pause`, `device.resume`, `device.cancel`, and `device.skip_objects` by calling existing `MachineObject` commands.
- [ ] Require an explicit request flag such as `allow_physical_printer_action: true` for send, cancel, pause, resume, and skip operations.

Acceptance:

- The server can list the H2D, report live status, send a small test plate, pause, resume, and cancel a test job when explicitly authorized.
- When the networking plugin is missing, responses fail clearly with a plugin-missing error and do not pretend printer operations succeeded.

### 8. Camera capture and monitor loop

- [ ] Implement `camera.snapshot` by extracting the still-image path from `MediaPlayCtrl::start_device_image_flow` into an automation-safe helper that uses `FileTransferObject::DownloadMemFile("mem:/26")`.
- [ ] Implement `camera.stream_frame` using the local RTSP/RTSPS URL when available and the networking plugin URL helper when remote protocols are required.
- [ ] Store captured frames under an agent-controlled artifact directory with timestamped filenames and paired telemetry JSON.
- [ ] Implement `monitor.start` as a polling loop that captures status plus images at a configured interval.
- [ ] Add policy hooks for external analysis: the API records images and telemetry; Codex or another LLM evaluates them and sends an explicit `device.pause` or `device.cancel` command if configured thresholds are crossed.
- [ ] Never auto-cancel without an explicit monitor policy that includes `allow_physical_printer_action: true`.

Acceptance:

- The server captures a current printer-bed still image from the H2D.
- The monitor loop writes a time-ordered image/telemetry series.
- A configured dry-run monitor reports the action it would take without controlling the printer.

### 9. Tests and verification

- [ ] Add unit tests for agent JSON parsing and schema-version errors.
- [ ] Add project roundtrip tests: load, inspect, edit, save, reload.
- [ ] Add cut tests for planar cut, existing connector types, dovetail connector generation, and recursive H2D partitioning.
- [ ] Add device API tests with a fake `NetworkAgent`/`MachineObject` where possible.
- [ ] Add one real-printer verification script that requires explicit environment variables for device id, LAN IP, access code, and `ALLOW_PHYSICAL_PRINTER_ACTION=1`.

Acceptance:

```bash
cmake --build build/$(uname -m) --config RelWithDebInfo --target BambuStudio
ctest --test-dir build/$(uname -m) --output-on-failure
```

passes for non-printer tests, and the real-printer script is opt-in only.

### 10. Codex-facing runbook

- [ ] Document request/response JSON examples in `docs/agent-api/`.
- [ ] Add examples for inspecting a 3MF, adding a negative volume, cutting with dovetails, slicing all plates, sending one H2D plate, capturing a snapshot, and running a dry-run monitor.
- [ ] Add a safety section for physical printer commands and networking-plugin requirements.
- [ ] Add a troubleshooting section for missing plugin, invalid access code, LAN-only liveview disabled, slicing failure, and non-manifold cut output.

Acceptance:

- A Codex run can use only documented JSON commands to load a model, partition it for H2D printing, save the project, slice it, send a test job, capture a bed image, and pause/cancel only when explicitly authorized.

## Milestone Order

1. Build baseline and agent batch entrypoint.
2. 3MF inspect/edit/save roundtrip.
3. Plate/slice/export API.
4. Headless cut backend with existing connector types.
5. Dovetail connector type and H2D auto-partitioning.
6. Local server, device status, send, pause, resume, cancel, and skip.
7. Camera snapshot/stream-frame extraction and monitor loop.
8. Real H2D verification and Codex runbook.

## Key Risk Controls

- Keep GUI behavior unchanged unless an `--agent-*` option is present.
- Treat physical printer actions as privileged and opt-in.
- Keep network-plugin failures explicit.
- Avoid direct 3MF XML edits except for diagnostic inspection.
- Validate every generated cut piece by reload, arrange, and slice.
- Preserve existing local fork changes around connector behavior during extraction.
