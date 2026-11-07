# UART protocol reference

[Project](../README.md) · [Usage](USAGE.md) · [Implementation](../esc_uart_gui_tkinter.py)

The frames below are observations from the original project logs. They describe the tested protocol, rather than a universal scooter interface. The source remains the reference for the application's current behavior.

TX CRC bytes are appended in big-endian order. The decoder can display `CRC ?` for longer RX frames; the interpretation of those RX checksums remains unresolved.

## TX frame format

The tool uses this frame format:

```text
CMD_HI CMD_LO LEN VALUE... CRC32_MPEG2
```

Even an empty read command should be sent with `LEN=01` and value `00`:

```text
XX XX 01 00 + CRC
```

3-byte payloads such as:

```text
XX XX 00 + CRC
```

are not used, because they did not work correctly in testing.


## CRC32/MPEG-2

CRC parameters:

```text
poly   = 0x04C11DB7
init   = 0xFFFFFFFF
refin  = false
refout = false
xorout = 0x00000000
```

Example:

```text
START payload: 00 02 01 01
TX frame:      00 02 01 01 12 6D 58 1E
```


## Confirmed commands

### Start / Stop

```text
START: 00 02 01 01 + CRC
STOP:  00 02 01 00 + CRC
```

Typical responses:

```text
80 02 01 01 + CRC
80 02 01 00 + CRC
```

### Battery

```text
BATTERY: 01 02 01 00 + CRC
```

Typical response:

```text
81 02 01 XX + CRC
```

`XX` is the battery percentage.

Examples:

```text
64 = 100%
20 = 32%
00 = 0%
```

### Speed

```text
SPEED: 00 04 01 01 + CRC
```

Typical response:

```text
80 04 02 XX YY ...
```

Speed value:

```text
uint16_be(XX YY) / 10.0
```

Examples:

```text
00 22 = 34  -> 3.4 km/h
00 21 = 33  -> 3.3 km/h
00 0B = 11  -> 1.1 km/h
00 38 = 56  -> 5.6 km/h
00 EB = 235 -> 23.5 km/h
```

### Model

```text
MODEL: 00 01 01 00 + CRC
```

Example response from logs:

```text
80 01 07 58 4C 54 38 35 35 53 ...
```

ASCII:

```text
XLT855S
```

### Version A / Version B

```text
VERSION A: 02 02 01 00 + CRC
VERSION B: 02 03 01 00 + CRC
```

Examples from logs:

```text
VERSION A: T_1_3_4
VERSION B: T_1_2_1
```

### Status 16

```text
STATUS 16: 00 08 01 00 + CRC
```

Typical response:

```text
80 08 02 XX YY ...
```

The decoder displays it as `STAT16=0xXXXX`.

### Ping

```text
PING: 0E 00 01 00 + CRC
```

Typical response:

```text
8E 00 01 01 + CRC
```

### Buzzer / AUX / Battery lock

```text
BUZZER OFF:    03 03 01 00 + CRC
BUZZER ON:     03 03 01 01 + CRC
AUX OFF:       03 04 01 00 + CRC
AUX ON:        03 04 01 01 + CRC
BATTERY OPEN:  03 05 01 00 + CRC
BATTERY CLOSE: 03 05 01 01 + CRC
```

Use control commands carefully.



## Experimental commands

The GUI also contains probe variants of several read commands. An echo or ACK does not prove a command's meaning. The suspected OTA/flash families `0F 02` and `0F 03` are blocked by the application.

Start with documented reads and small scan ranges on a bench setup. Preserve the logs and record the controller model and firmware versions with each result.
