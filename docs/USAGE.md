# Setup and usage

[Project](../README.md) · [Protocol](PROTOCOL.md)

## Environment

Use Python 3.10 or newer with Tkinter. `pyserial` is the only third-party Python dependency. On Linux, Tkinter may be supplied separately by the operating system.

From the repository directory, create an isolated environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Or on Linux:

```bash
source .venv/bin/activate
```

Then install and start:

```bash
python -m pip install -r requirements.txt
python esc_uart_gui_tkinter.py
```

## First connection

1. Confirm the controller's UART pins and signal voltage before connecting the adapter.
2. Select the serial port and baud rate. The application defaults to **9600 baud**.
3. Connect and begin with known read commands such as battery, model or firmware version.
4. Inspect both the raw TX/RX frames and the decoded result before trying scans or control commands.

The default START heartbeat interval is **300 ms**. START/STOP, battery-lock, AUX and buzzer commands change hardware state; heartbeat also repeats a command. Use a bench setup with the wheel clear.

## Generated files

Paths are relative to the directory from which the program is launched.

| Path | Content |
| --- | --- |
| `esc_uart_gui_log.txt` | TX/RX log |
| `esc_uart_gui_response_changes.txt` | Changed or interesting responses |
| `esc_uart_scan_progress.json` | Regular scan progress |
| `smart_scan_progress.json` | Smart scan progress |
| `smart_family_logs/` | Per-family scan logs |
| `esc_uart_gui_window.json` | Window geometry |

These runtime files are excluded by `.gitignore`. Keep useful captures separately when reporting findings.

## Offline checks

Syntax check:

```bash
python -m py_compile esc_uart_gui_tkinter.py
```

Run the existing protocol self-test without opening the GUI or connecting to a serial port:

```bash
python -c "import esc_uart_gui_tkinter as gui; gui.run_selftest(); print('Self-test OK')"
```

Importing the module still requires Tkinter and `pyserial`. The self-test covers framing, CRC and predefined payloads; it does not verify a connected controller.
