import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
from pathlib import Path
from queue import Queue, Empty

import serial
import serial.tools.list_ports

BAUD_DEFAULT = 38400
TIMEOUT = 0.05
RX_FRAME_GAP = 0.05
HEARTBEAT_DEFAULT_MS = 300

LOG_FILE = Path("esc_uart_gui_log.txt")
RESP_LOG_FILE = Path("esc_uart_gui_response_changes.txt")


# ============================================================
# CRC / helpers
# ============================================================
def crc32_mpeg2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= (b << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc & 0xFFFFFFFF


def build_frame(payload4: bytes) -> bytes:
    if len(payload4) != 4:
        raise ValueError("Payload musi mieć dokładnie 4 bajty")
    return payload4 + crc32_mpeg2(payload4).to_bytes(4, "big")


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex(s: str) -> bytes:
    s = s.strip().replace("0x", "").replace(",", " ").replace(";", " ")
    parts = [p for p in s.split() if p]
    return bytes(int(p, 16) for p in parts)


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log_append(path: Path, line: str):
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def list_ports() -> list[str]:
    return [p.device for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device)]


KNOWN = {
    "START": bytes([0x00, 0x02, 0x01, 0x01]),
    "STOP": bytes([0x00, 0x02, 0x01, 0x00]),
    "BAT_OPEN": bytes([0x03, 0x05, 0x01, 0x00]),
    "BAT_CLOSE": bytes([0x03, 0x05, 0x01, 0x01]),
    "MODE1": bytes([0x00, 0x03, 0x01, 0x01]),
    "MODE0": bytes([0x00, 0x03, 0x01, 0x00]),
}


def run_selftest():
    assert crc32_mpeg2(b"123456789") == 0x0376E6E7
    assert parse_hex("AA 55 01 01") == bytes([0xAA, 0x55, 0x01, 0x01])
    assert parse_hex("0xAA,0x55;0x01 0x01") == bytes([0xAA, 0x55, 0x01, 0x01])
    assert hx(bytes([0xAA, 0x55, 0x01, 0x01])) == "AA 55 01 01"
    f = build_frame(bytes([0xAA, 0x55, 0x01, 0x01]))
    assert len(f) == 8
    assert f[:4] == bytes([0xAA, 0x55, 0x01, 0x01])


class SerialWorker:
    def __init__(self, ui_callback):
        self.ser = None
        self.running = False
        self.rx_thread = None
        self.ui_callback = ui_callback
        self.rx_queue = Queue()
        self.hb_thread = None
        self.hb_stop = threading.Event()

    def connect(self, port: str, baud: int):
        self.disconnect()
        self.ser = serial.Serial(port, baud, timeout=TIMEOUT)
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()

    def disconnect(self):
        self.stop_heartbeat()
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _rx_loop(self):
        buf = bytearray()
        last_t = time.time()

        while self.running and self.ser:
            try:
                data = self.ser.read(256)
                now = time.time()

                if data:
                    buf.extend(data)
                    last_t = now
                elif buf and (now - last_t) > RX_FRAME_GAP:
                    frame = bytes(buf)
                    self.rx_queue.put(frame)
                    self.ui_callback("RX", frame, "")
                    buf.clear()
            except Exception as e:
                self.ui_callback("ERR", str(e).encode("utf-8", errors="ignore"), "RX")
                break

    def send_payload4(self, payload4: bytes, note: str = "") -> bytes:
        if not self.is_connected():
            raise RuntimeError("Brak połączenia z portem COM")
        frame = build_frame(payload4)
        self.ser.write(frame)
        self.ser.flush()
        self.ui_callback("TX", frame, note)
        return frame

    def send_raw8(self, frame8: bytes, note: str = "") -> bytes:
        if not self.is_connected():
            raise RuntimeError("Brak połączenia z portem COM")
        if len(frame8) != 8:
            raise ValueError("Raw frame musi mieć dokładnie 8 bajtów")
        self.ser.write(frame8)
        self.ser.flush()
        self.ui_callback("TX", frame8, note)
        return frame8

    def clear_rx_queue(self):
        while True:
            try:
                self.rx_queue.get_nowait()
            except Empty:
                break

    def collect_responses(self, window_s: float) -> list[bytes]:
        end = time.time() + window_s
        out = []
        while time.time() < end:
            try:
                out.append(self.rx_queue.get(timeout=0.05))
            except Empty:
                pass
        return out

    def start_heartbeat(self, payload4: bytes, interval_ms: int, note: str = "HB"):
        self.stop_heartbeat()
        self.hb_stop.clear()

        def worker():
            while not self.hb_stop.is_set():
                try:
                    self.send_payload4(payload4, note)
                except Exception:
                    break
                if self.hb_stop.wait(interval_ms / 1000.0):
                    break

        self.hb_thread = threading.Thread(target=worker, daemon=True)
        self.hb_thread.start()

    def stop_heartbeat(self):
        self.hb_stop.set()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESC UART Safe GUI")
        self.geometry("1180x760")
        self.minsize(1000, 650)

        self.worker = SerialWorker(self.on_serial_event)
        self.safe_mode = tk.BooleanVar(value=True)
        self.baud_var = tk.StringVar(value=str(BAUD_DEFAULT))
        self.port_var = tk.StringVar()
        self.hb_interval_var = tk.StringVar(value=str(HEARTBEAT_DEFAULT_MS))
        self.manual_hex_var = tk.StringVar()
        self.scan_window_var = tk.StringVar(value="0.45")
        self.scan_group_var = tk.StringVar(value="00")
        self.scan_cmd_from_var = tk.StringVar(value="00")
        self.scan_cmd_to_var = tk.StringVar(value="0F")
        self.scan_value_from_var = tk.StringVar(value="00")
        self.scan_value_to_var = tk.StringVar(value="01")
        self.scan_len_var = tk.StringVar(value="01")
        self.scan_delay_var = tk.StringVar(value="200")

        self.autotest_index = 0
        self.autotest_running = False
        self.autotest_steps = [
            ("START", KNOWN["START"]),
            ("STOP", KNOWN["STOP"]),
            ("BAT_CLOSE", KNOWN["BAT_CLOSE"]),
            ("BAT_OPEN", KNOWN["BAT_OPEN"]),
            ("MODE1", KNOWN["MODE1"]),
            ("MODE0", KNOWN["MODE0"]),
        ]

        self._build_ui()
        self.refresh_ports()
        self.after(100, self._tick)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="COM:").pack(side="left")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=14, state="readonly")
        self.port_combo.pack(side="left", padx=(5, 8))

        ttk.Button(top, text="Odśwież", command=self.refresh_ports).pack(side="left")
        ttk.Label(top, text="  Baud:").pack(side="left")
        ttk.Entry(top, textvariable=self.baud_var, width=8).pack(side="left", padx=(5, 8))
        ttk.Button(top, text="Połącz", command=self.connect_port).pack(side="left")
        ttk.Button(top, text="Rozłącz", command=self.disconnect_port).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(top, text="Safe mode", variable=self.safe_mode).pack(side="left", padx=(16, 0))
        ttk.Button(top, text="Selftest", command=self.selftest_ui).pack(side="left", padx=(12, 0))

        ttk.Button(top, text="Otwórz log ogólny", command=lambda: self.open_file(LOG_FILE)).pack(side="right")
        ttk.Button(top, text="Otwórz log zmian", command=lambda: self.open_file(RESP_LOG_FILE)).pack(side="right", padx=(0, 6))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.Frame(body, padding=8)
        right = ttk.Frame(body, padding=8)
        body.add(left, weight=0)
        body.add(right, weight=1)

        # left controls
        conn_box = ttk.LabelFrame(left, text="Szybkie komendy", padding=8)
        conn_box.pack(fill="x", pady=(0, 10))

        for i, name in enumerate(["START", "STOP", "BAT_OPEN", "BAT_CLOSE", "MODE1", "MODE0"]):
            ttk.Button(conn_box, text=name, command=lambda n=name: self.send_known(n)).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4
            )
        conn_box.columnconfigure(0, weight=1)
        conn_box.columnconfigure(1, weight=1)

        manual = ttk.LabelFrame(left, text="Ręczne wysyłanie", padding=8)
        manual.pack(fill="x", pady=(0, 10))
        ttk.Label(manual, text="4 bajty lub 8 bajtów hex:").pack(anchor="w")
        ttk.Entry(manual, textvariable=self.manual_hex_var).pack(fill="x", pady=6)

        row = ttk.Frame(manual)
        row.pack(fill="x")
        ttk.Button(row, text="Wyślij auto", command=self.send_manual_auto).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Policz CRC", command=self.show_crc).pack(side="left", expand=True, fill="x", padx=(6, 0))

        hb = ttk.LabelFrame(left, text="Heartbeat", padding=8)
        hb.pack(fill="x", pady=(0, 10))
        ttk.Label(hb, text="Interwał ms:").pack(anchor="w")
        ttk.Entry(hb, textvariable=self.hb_interval_var).pack(fill="x", pady=6)
        row = ttk.Frame(hb)
        row.pack(fill="x")
        ttk.Button(row, text="HB START", command=lambda: self.start_hb("START")).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="HB STOP", command=self.stop_hb).pack(side="left", expand=True, fill="x", padx=(6, 0))

        scan = ttk.LabelFrame(left, text="Scan zakresów", padding=8)
        scan.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(scan)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Group:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_group_var, width=6).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="Len:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_len_var, width=6).pack(side="left", padx=(4, 0))

        row = ttk.Frame(scan)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="CMD od:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_cmd_from_var, width=6).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="do:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_cmd_to_var, width=6).pack(side="left", padx=(4, 0))

        row = ttk.Frame(scan)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="VAL od:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_value_from_var, width=6).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="do:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_value_to_var, width=6).pack(side="left", padx=(4, 0))

        row = ttk.Frame(scan)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Okno odp. [s]:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_window_var, width=8).pack(side="left", padx=(4, 10))
        ttk.Label(row, text="Delay [ms]:").pack(side="left")
        ttk.Entry(row, textvariable=self.scan_delay_var, width=8).pack(side="left", padx=(4, 0))

        ttk.Button(scan, text="Uruchom scan zakresu", command=self.safe_scan).pack(fill="x")

        auto = ttk.LabelFrame(left, text="Auto test krokowy", padding=8)
        auto.pack(fill="x", pady=(0, 10))
        self.autotest_label = ttk.Label(auto, text="Nie uruchomiono")
        self.autotest_label.pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(auto)
        row.pack(fill="x")
        ttk.Button(row, text="Start", command=self.autotest_start).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Dalej", command=self.autotest_next).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(row, text="Powtórz", command=self.autotest_repeat).pack(side="left", expand=True, fill="x")

        row2 = ttk.Frame(auto)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="Cofnij", command=self.autotest_back).pack(side="left", expand=True, fill="x")
        ttk.Button(row2, text="Stop", command=self.autotest_stop).pack(side="left", expand=True, fill="x", padx=(6, 0))

        info = ttk.LabelFrame(left, text="Jak używać", padding=8)
        info.pack(fill="both", expand=True)

        msg = (
            "1. Wybierz COM i kliknij Połącz.\n"
            "2. Testuj najpierw START / STOP ręcznie.\n"
            "3. Safe mode pilnuje potwierdzeń przy ryzykownych akcjach.\n"
            "4. Scan zakresów bada group/cmd/value z pól obok.\n"
            "5. Log zmian odpowiedzi zapisuje tylko ciekawe różnice."
        )
        ttk.Label(info, text=msg, justify="left").pack(anchor="w")

        # right logs
        right_top = ttk.Frame(right)
        right_top.pack(fill="x")
        ttk.Button(right_top, text="Wyczyść log w oknie", command=self.clear_log).pack(side="left")
        ttk.Button(right_top, text="Zapisz log jako...", command=self.save_log_as).pack(side="left", padx=6)

        self.log_text = ScrolledText(right, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text.configure(state="disabled")

    # --------------------------------------------------------
    # Utility UI
    # --------------------------------------------------------
    def log_ui(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def save_log_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        content = self.log_text.get("1.0", "end")
        Path(path).write_text(content, encoding="utf-8")
        messagebox.showinfo("OK", f"Zapisano: {path}")

    def open_file(self, path: Path):
        if not path.exists():
            messagebox.showwarning("Brak pliku", f"Plik jeszcze nie istnieje:\n{path}")
            return
        try:
            import os
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("Błąd", str(e))

    def ask_safe(self, text: str) -> bool:
        if not self.safe_mode.get():
            return True
        return messagebox.askokcancel("Potwierdź", text)

    def selftest_ui(self):
        try:
            run_selftest()
            messagebox.showinfo("Selftest", "Selftest OK")
        except Exception as e:
            messagebox.showerror("Selftest", str(e))

    # --------------------------------------------------------
    # Serial event bridge
    # --------------------------------------------------------
    def on_serial_event(self, kind: str, data: bytes, note: str):
        if kind in ("RX", "TX"):
            line = f"[{ts()}] {kind} {hx(data)}"
            if note:
                line += f" | {note}"
            log_append(LOG_FILE, line)
        else:
            line = f"[{ts()}] {kind} {data.decode(errors='ignore')}"
        self.after(0, lambda l=line: self.log_ui(l))

    def _tick(self):
        status = "POŁĄCZONO" if self.worker.is_connected() else "ROZŁĄCZONO"
        self.title(f"ESC UART Safe GUI — {status}")
        self.after(250, self._tick)

    # --------------------------------------------------------
    # Connect / disconnect
    # --------------------------------------------------------
    def refresh_ports(self):
        ports = list_ports()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def connect_port(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Brak COM", "Wybierz port COM")
            return
        try:
            baud = int(self.baud_var.get().strip())
            self.worker.connect(port, baud)
            self.log_ui(f"[{ts()}] INFO Połączono z {port} @ {baud}")
        except Exception as e:
            messagebox.showerror("Błąd połączenia", str(e))

    def disconnect_port(self):
        self.worker.disconnect()
        self.log_ui(f"[{ts()}] INFO Rozłączono")

    # --------------------------------------------------------
    # Send actions
    # --------------------------------------------------------
    def send_known(self, name: str):
        if name in ("BAT_OPEN", "BAT_CLOSE"):
            if not self.ask_safe(f"Wysłać {name}? To może zmienić stan blokady baterii."):
                return
        try:
            self.worker.send_payload4(KNOWN[name], name)
        except Exception as e:
            messagebox.showerror("Błąd", str(e))

    def send_manual_auto(self):
        text = self.manual_hex_var.get().strip()
        if not text:
            return
        try:
            raw = parse_hex(text)
            if len(raw) == 4:
                if not self.ask_safe(f"Wyślij 4 bajty z auto CRC?\n{hx(raw)}"):
                    return
                self.worker.send_payload4(raw, "manual4")
            elif len(raw) == 8:
                if not self.ask_safe(f"Wyślij surowe 8 bajtów?\n{hx(raw)}"):
                    return
                self.worker.send_raw8(raw, "manual8")
            else:
                messagebox.showwarning("Zła długość", "Podaj 4 albo 8 bajtów hex")
        except Exception as e:
            messagebox.showerror("Błąd", str(e))

    def show_crc(self):
        text = self.manual_hex_var.get().strip()
        try:
            raw = parse_hex(text)
            if len(raw) != 4:
                messagebox.showwarning("Zła długość", "Tu liczmy CRC tylko dla 4 bajtów")
                return
            frame = build_frame(raw)
            messagebox.showinfo("Pełna ramka", hx(frame))
        except Exception as e:
            messagebox.showerror("Błąd", str(e))

    # --------------------------------------------------------
    # Heartbeat
    # --------------------------------------------------------
    def start_hb(self, name: str):
        try:
            ms = int(self.hb_interval_var.get().strip())
            if ms <= 0:
                raise ValueError("Interwał musi być > 0")
            if not self.ask_safe(f"Uruchomić heartbeat {name} co {ms} ms?"):
                return
            self.worker.start_heartbeat(KNOWN[name], ms, f"HB:{name}")
            self.log_ui(f"[{ts()}] INFO Heartbeat ON {name} co {ms} ms")
        except Exception as e:
            messagebox.showerror("Błąd", str(e))

    def stop_hb(self):
        self.worker.stop_heartbeat()
        self.log_ui(f"[{ts()}] INFO Heartbeat OFF")

    # --------------------------------------------------------
    # Scan ranges
    # --------------------------------------------------------
    def safe_scan(self):
        if not self.ask_safe("Uruchomić scan zakresu? To wyśle serię komend z podanego zakresu."):
            return

        try:
            group = int(self.scan_group_var.get().strip(), 16)
            cmd_from = int(self.scan_cmd_from_var.get().strip(), 16)
            cmd_to = int(self.scan_cmd_to_var.get().strip(), 16)
            val_from = int(self.scan_value_from_var.get().strip(), 16)
            val_to = int(self.scan_value_to_var.get().strip(), 16)
            fixed_len = int(self.scan_len_var.get().strip(), 16)
            window_s = float(self.scan_window_var.get().strip())
            delay_ms = int(self.scan_delay_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Błąd",
                "Zakresy scanu wpisuj jako HEX, np. 00 / 0F / 01. "
                "Okno odpowiedzi jako liczba, delay w ms."
            )
            return

        if not (
            0 <= group <= 0xFF and
            0 <= cmd_from <= 0xFF and
            0 <= cmd_to <= 0xFF and
            0 <= val_from <= 0xFF and
            0 <= val_to <= 0xFF and
            0 <= fixed_len <= 0xFF
        ):
            messagebox.showerror("Błąd", "Każde pole HEX musi być w zakresie 00..FF")
            return

        if cmd_from > cmd_to or val_from > val_to:
            messagebox.showerror("Błąd", "Początek zakresu nie może być większy niż koniec")
            return

        if delay_ms < 0 or window_s < 0:
            messagebox.showerror("Błąd", "Delay i okno odpowiedzi nie mogą być ujemne")
            return

        def worker():
            baseline = None
            hits = 0
            total = 0
            self.log_ui(
                f"[{ts()}] INFO SCAN START "
                f"group={group:02X} cmd={cmd_from:02X}-{cmd_to:02X} "
                f"val={val_from:02X}-{val_to:02X} len={fixed_len:02X}"
            )

            for cmd in range(cmd_from, cmd_to + 1):
                for val in range(val_from, val_to + 1):
                    payload = bytes([group, cmd, fixed_len, val])
                    total += 1
                    try:
                        self.worker.clear_rx_queue()
                        frame = self.worker.send_payload4(payload, f"scan:{hx(payload)}")
                        responses = self.worker.collect_responses(window_s)
                        sign = tuple(hx(r) for r in responses)

                        if baseline is None:
                            baseline = sign

                        if sign != baseline:
                            hits += 1
                            log_append(RESP_LOG_FILE, "=" * 70)
                            log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} CMD {hx(payload)}")
                            log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} TX  {hx(frame)}")
                            if responses:
                                for i, r in enumerate(responses, start=1):
                                    log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} RX{i:02d} {hx(r)}")
                            else:
                                log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} RX <brak odpowiedzi>")
                            self.log_ui(f"[{ts()}] DIFF {hx(payload)}")

                        time.sleep(delay_ms / 1000.0)
                    except Exception as e:
                        self.log_ui(f"[{ts()}] ERR scan {e}")
                        return

            self.log_ui(f"[{ts()}] INFO SCAN DONE hits={hits} total={total}")

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------
    # Autotest step-by-step
    # --------------------------------------------------------
    def autotest_start(self):
        self.autotest_running = True
        self.autotest_index = 0
        self._autotest_show()

    def autotest_stop(self):
        self.autotest_running = False
        self.autotest_label.config(text="Zatrzymano")

    def _autotest_show(self):
        if not self.autotest_running:
            return
        if self.autotest_index < 0:
            self.autotest_index = 0
        if self.autotest_index >= len(self.autotest_steps):
            self.autotest_label.config(text="Auto test zakończony")
            self.autotest_running = False
            return

        name, payload = self.autotest_steps[self.autotest_index]
        self.autotest_label.config(
            text=f"Krok {self.autotest_index + 1}/{len(self.autotest_steps)}: {name} [{hx(payload)}]"
        )

    def autotest_fire_current(self):
        if not self.autotest_running:
            return

        name, payload = self.autotest_steps[self.autotest_index]
        if name in ("BAT_OPEN", "BAT_CLOSE"):
            if not self.ask_safe(f"Auto test chce wysłać {name}. Kontynuować?"):
                return

        def worker():
            try:
                self.worker.clear_rx_queue()
                frame = self.worker.send_payload4(payload, f"autotest:{name}")
                responses = self.worker.collect_responses(0.45)

                log_append(RESP_LOG_FILE, "=" * 70)
                log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} AUTOTEST {name}")
                log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} TX {hx(frame)}")
                if responses:
                    for i, r in enumerate(responses, start=1):
                        log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} RX{i:02d} {hx(r)}")
                else:
                    log_append(RESP_LOG_FILE, f"{time.strftime('%Y-%m-%d %H:%M:%S')} RX <brak odpowiedzi>")
            except Exception as e:
                self.log_ui(f"[{ts()}] ERR autotest {e}")

        threading.Thread(target=worker, daemon=True).start()

    def autotest_next(self):
        if not self.autotest_running:
            self.autotest_start()
        self.autotest_fire_current()
        self.autotest_index += 1
        self._autotest_show()

    def autotest_repeat(self):
        if not self.autotest_running:
            self.autotest_start()
        self.autotest_fire_current()

    def autotest_back(self):
        if not self.autotest_running:
            self.autotest_start()
            return
        self.autotest_index -= 1
        if self.autotest_index < 0:
            self.autotest_index = 0
        self._autotest_show()


if __name__ == "__main__":
    run_selftest()
    app = App()
    app.mainloop()