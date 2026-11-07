# ESC UART GUI

Narzędzie w Pythonie/Tkinter do logowania, dekodowania i badania komunikacji UART sterownika hulajnogi. Projekt powstał na podstawie pomiarów sprzętu i logów TX/RX.

[English](README.md) · [Instalacja i obsługa](docs/USAGE.md) · [Opis protokołu](docs/PROTOCOL.md) · [Kod programu](esc_uart_gui_tkinter.py)

## Uruchomienie

Potrzebne są Python 3.10+, Tkinter, `pyserial` i adapter USB–UART. Program jest przeznaczony dla Windows i Linuksa.

```bash
python -m pip install -r requirements.txt
python esc_uart_gui_tkinter.py
```

Wybierz port, zacznij od **9600 baud** i połącz się ze sterownikiem.

## Możliwości

- Gotowe odczyty oraz komendy sterujące, ręczne payloady HEX i automatyczne CRC32/MPEG-2.
- Dekodowanie baterii, prędkości, modelu, wersji firmware i statusu.
- Logi TX/RX, skan zakresów, Smart Tree Scan i autotest krokowy.
- Polski i angielski interfejs oraz zapamiętywanie geometrii okna.

Protokół jest rozpoznany częściowo. Długie odpowiedzi mogą mieć oznaczenie `CRC ?`. Komendy zmieniają stan sprzętu; próby wykonuj na stanowisku z uniesionym kołem. Program blokuje podejrzane rodziny OTA/flash `0F 02` i `0F 03`, ale nie potwierdza bezpieczeństwa pozostałych nieznanych komend.

Szczegółowe komendy i przykłady ramek są w [dokumentacji protokołu](docs/PROTOCOL.md). Możesz też [postawić autorowi kawę](https://buycoffee.to/arasusx-cyber).
