"""mentions_part, tested on its own (review 2026-10-22, second pass): a table of real part numbers against the first
lines of their own datasheets (must name the part) and of sibling parts' datasheets (must not), plus properties that
hold for any part number: case and #PBF / /NOPB tails change nothing, the MPN itself always names the part, a text too
short or a part number with no digits is None (can't be told, never a rejection), and odd input never raises.
Plain functions (tests.run_all, no pytest).
    python3 -m tests.test_review_1022_mentions"""
import random

from ecadlib import datasheet as D

PAD = " Package information, ordering information, absolute maximum ratings and electrical characteristics follow."

POS = [
 ("STM32F103C8T6", "STM32F103x8 STM32F103xB Medium-density performance line Arm-based 32-bit MCU with 64 or 128 KB Flash"),
 ("STM32G431CBU6", "STM32G431x6 STM32G431x8 STM32G431xB Arm Cortex-M4 32-bit MCU+FPU, 170 MHz"),
 ("LM317T", "LM317 3-Terminal Adjustable Regulator. The LM317 device is an adjustable three-terminal positive-voltage regulator"),
 ("NE555P", "NA555, NE555, SA555, SE555 Precision Timers. These devices are precision timing circuits"),
 ("TPS62133RGTR", "TPS6213x 3-V to 17-V, 3-A Step-Down Converter in 3x3 QFN Package"),
 ("ATMEGA328P-AU", "ATmega328P 8-bit AVR Microcontroller with 32K Bytes In-System Programmable Flash"),
 ("ATMEGA328P-AU", "ATmega48A/PA/88A/PA/168A/PA/328/P megaAVR Data Sheet. Features High Performance, Low Power AVR 8-Bit Microcontroller"),
 ("SN74HC595N", "SNx4HC595 8-Bit Shift Registers With 3-State Output Registers"),
 ("MAX3232ECAE+", "MAX3222E/MAX3232E/MAX3237E/MAX3241E/MAX3246E +-15kV ESD-Protected, Down to 10nA, 3.0V to 5.5V RS-232 Transceivers"),
 ("ESP32-S3-WROOM-1-N8R8", "ESP32-S3-WROOM-1 ESP32-S3-WROOM-1U Datasheet Version 1.2 2.4 GHz Wi-Fi + Bluetooth LE module"),
 ("LM358DR", "LM158, LM258, LM358, LM2904 Dual Operational Amplifiers"),
 ("BSS138", "BSS138 N-Channel Logic Level Enhancement Mode Field Effect Transistor General Description"),
 ("2N7002", "2N7002 N-channel Trench MOSFET 60 V, 300 mA. Product data sheet"),
 ("AMS1117-3.3", "AMS1117 1A LOW DROPOUT VOLTAGE REGULATOR. FEATURES Three Terminal Adjustable or Fixed Voltages"),
 ("CH340C", "CH340 Datasheet. USB to serial chip CH340, the CH340 is a USB bus convert chip"),
 ("TLV70033DDCR", "TLV700xx 200-mA, Low-IQ, Low-Dropout Regulator for Portable Devices"),
 ("LTC4060EDHC#PBF", "LTC4060 Standalone Linear NiMH/NiCd Fast Battery Charger. Features Complete Fast Charger"),
 ("RP2040", "RP2040 Datasheet A microcontroller by Raspberry Pi. Colophon copyright Raspberry Pi Ltd"),
 ("W25Q128JVSIQ", "W25Q128JV 3V 128M-BIT SERIAL FLASH MEMORY WITH DUAL/QUAD SPI. Revision H"),
 ("ADS1115IDGSR", "ADS111x Ultra-Small, Low-Power, I2C-Compatible, 860-SPS, 16-Bit ADCs With Internal Reference"),
 ("PIC16F877A-I/P", "PIC16F87XA Data Sheet 28/40/44-Pin Enhanced Flash Microcontrollers"),
  ("TPS7A4701RGWR", "TPS7A4700, TPS7A4701 36-V, 1-A, RF LDO Voltage Regulator"),
 ("MCP73831T-2ACI/OT", "MCP73831/2 Miniature Single-Cell, Fully Integrated Li-Ion, Li-Polymer Charge Management Controllers"),
 ("LM2596S-5.0", "LM2596 SIMPLE SWITCHER Power Converter 150-kHz 3-A Step-Down Voltage Regulator"),
 ("NCP1117ST33T3G", "NCP1117, NCV1117 1.0 A Low-Dropout Positive Fixed and Adjustable Voltage Regulators"),
 ("IRLML6344TRPBF", "IRLML6344TRPbF HEXFET Power MOSFET VDS 30 V RDS(on) max"),
 ("XC6206P332MR", "XC6206 Series Positive Voltage Regulators, Low ESR Cap. Compatible, 250mA"),
 ("TJA1051T/3", "TJA1051 High-speed CAN transceiver Product data sheet Rev. 9"),
 ("74HC595D", "74HC595; 74HCT595 8-bit serial-in, serial or parallel-out shift register with output latches; 3-state"),
 ("BAT54S", "BAT54 series Schottky barrier diodes. Product data sheet. Planar Schottky barrier diodes"),
 ("1N4148W", "1N4148W SURFACE MOUNT FAST SWITCHING DIODE Features Fast Switching Speed"),
 ("SM6T100A", "SM6T Series 600 W TVS diode. Datasheet - production data. Peak pulse power 600 W"),
 ("AD5274BRMZ-20", "AD5272/AD5274 1024-/256-Position, 1% Resistor Tolerance Error, I2C Interface and 50-TP Memory Digital Rheostat"),
 ("MAX22205ATJ+", "MAX22201-MAX22207 Dual H-Bridge Motor Drivers with integrated current sensing"),
 ("C8051F320-GQ", "C8051F320/1 Full Speed USB Flash MCU Family. C8051F32x"),
 ("TPS2660xRGE", "TPS2660x 4.2 V to 55 V, 150 mA to 2.23 A Industrial eFuse with Integrated Reverse Input Polarity Protection"),
 ("LM1117IMPX-3.3/NOPB", "LM1117 800-mA, Low-Dropout Linear Regulator. Features Available in 1.8 V, 2.5 V, 3.3 V"),
 ("TPS54331DR", "TPS54331 3.5-V to 28-V Input, 3-A, 570-kHz Step-Down Converter With Eco-Mode"),
 ("ATTINY85-20PU", "ATtiny25/V / ATtiny45/V / ATtiny85/V 8-bit AVR Microcontroller with 2/4/8K Bytes In-System Programmable Flash"),
 ("STM32L476RGT6", "STM32L476xx Ultra-low-power Arm Cortex-M4 32-bit MCU+FPU, 100DMIPS, up to 1MB Flash"),
 ("GD32F103C8T6", "GD32F103xx Datasheet. GigaDevice Semiconductor Inc. GD32F103xx ARM Cortex-M3 32-bit MCU"),
 ("LP5907MFX-3.3/NOPB", "LP5907 250-mA Ultra-Low-Noise Low-Dropout Regulator"),
 ("SS14", "SS12 THRU SS16 SURFACE MOUNT SCHOTTKY BARRIER RECTIFIER. Reverse voltage 20 to 60 Volts"),
 ("DRV8833PWPR", "DRV8833 Dual H-Bridge Motor Driver. Features Dual-H-Bridge Current-Control Motor Driver"),
 ("USBLC6-2SC6", "USBLC6-2 Very low capacitance ESD protection. Datasheet - production data"),
 ("TXS0108EPWR", "TXS0108E 8-Bit Bi-Directional, Level-Shifting, Voltage Translator for Open-Drain and Push-Pull"),
 ("NRF52832-QFAA-R", "nRF52832 Product Specification v1.4. Multiprotocol Bluetooth low energy, ANT and 2.4 GHz SoC"),
 ("BME280", "BME280 Combined humidity and pressure sensor. Bosch Sensortec Data sheet. Document revision 1.6"),
 ("MPU-6050", "MPU-6000 and MPU-6050 Product Specification Revision 3.4. InvenSense Inc."),
 ("ENS210-LQFM", "ENS210 Relative Humidity and Temperature Sensor with I2C Interface. Datasheet"),
 ("SI7021-A20-GM1R", "Si7021-A20 I2C HUMIDITY AND TEMPERATURE SENSOR. Features Precision Relative Humidity Sensor"),
]
NEG = [
 ("STM32F103C8T6", "STM32F030x4 STM32F030x6 STM32F030x8 STM32F030xC Value-line Arm-based 32-bit MCU"),
 ("STM32F103C8T6", "STM32L476xx Ultra-low-power Arm Cortex-M4 32-bit MCU+FPU, 100DMIPS, up to 1MB Flash"),
 ("TPS7A4701RGWR", "TPS7B6950-Q1 Automotive 150-mA, 40-V, low-IQ LDO regulator in small packages"),
 ("LTC4060EDHC#PBF", "LTC4054 Standalone Linear Li-Ion Battery Charger in ThinSOT. Features Programmable Charge Current"),
 ("PIC16F877A", "PIC16F15313/23 Full-Featured, Low Pin Count Microcontrollers with XLP"),
 ("ESP32-S3-WROOM-1", "ESP32-C3-MINI-1 ESP32-C3-MINI-1U Datasheet Version 1.4 Small-sized module"),
 ("ATMEGA328P-AU", "ATmega640/V-1280/V-1281/V-2560/V-2561/V 8-bit Atmel Microcontroller with 16/32/64KB"),
 ("LM317T", "LM337 3-Terminal Adjustable Negative Regulators. The LM337 is an adjustable negative voltage regulator"),
 ("W25Q128JVSIQ", "W25Q64JV 3V 64M-BIT SERIAL FLASH MEMORY WITH DUAL, QUAD SPI"),
 ("ADS1115IDGSR", "ADS101x Ultra-Small, Low-Power, I2C-Compatible, 3.3-kSPS, 12-Bit ADCs With Internal Reference"),
 ("CH340C", "CH9102F datasheet USB to UART chip. CH9102 is a USB bus converter chip"),
 ("AMS1117-3.3", "AMS1084 5A LOW DROPOUT VOLTAGE REGULATOR. FEATURES Three Terminal Adjustable"),
 ("74HC595D", "74HC164; 74HCT164 8-bit serial-in, parallel-out shift register. Product data sheet"),
 ("NE555P", "NA556, NE556, SA556, SE556 Dual Precision Timers. These devices provide two independent timing circuits"),
 ("XC6206P332MR", "XC6210 Series High Speed LDO Regulators, Low ESR Cap. Compatible, 700mA"),
 ("TPS54331DR", "TPS54332 3.5-A, 28-V Input, Step Down SWIFT DC-DC Converter With Eco-Mode"),
 ("DRV8833PWPR", "DRV8835 Dual Low-Voltage H-Bridge IC"),
 ("BME280", "BMP280 Digital Pressure Sensor. Bosch Sensortec Data sheet"),
 ("TXS0108EPWR", "TXB0108 8-Bit Bidirectional Voltage-Level Translator With Auto-Direction Sensing"),
 ("STM32G431CBU6", "STM32G474xB STM32G474xC STM32G474xE Arm Cortex-M4 32-bit MCU+FPU, 170 MHz"),
 ("GD32F103C8T6", "GD32F303xx Datasheet. GigaDevice Semiconductor Inc. ARM Cortex-M4 32-bit MCU"),
 ("SI7021-A20-GM1R", "Si7006-A20 I2C HUMIDITY AND TEMPERATURE SENSOR"),
 ("ENS210-LQFM", "ENS160 Digital Metal-Oxide Multi-Gas Sensor. Datasheet"),
 ("NRF52832-QFAA-R", "nRF52840 Product Specification v1.7. Bluetooth 5, Thread, Zigbee SoC"),
 ("TJA1051T/3", "TJA1057 High-speed CAN transceiver Product data sheet"),
]


def _wrong(rows, expected):
    return [(mpn, text[:50], got) for mpn, text in rows
            for got in [D.mentions_part([text + PAD], mpn)] if got is not expected]


def test_own_datasheets_name_the_part():
    assert _wrong(POS, True) == []


def test_sibling_parts_datasheets_do_not():
    assert _wrong(NEG, False) == []


def test_a_slash_list_with_shared_letters_names_each_member():
    # ATmega48A/PA/88A/PA/168A/PA/328/P: '328' after a slash is ATmega328
    text = "ATmega48A/PA/88A/PA/168A/PA/328/P megaAVR Data Sheet" + PAD
    assert D.mentions_part([text], "ATMEGA328P-AU") is True
    assert D.mentions_part([text], "ATMEGA168PA-AU") is True
    assert D.mentions_part([text], "ATMEGA2560-16AU") is False


def test_a_thru_range_names_the_parts_inside_it():
    text = "SS12 THRU SS16 SURFACE MOUNT SCHOTTKY BARRIER RECTIFIER" + PAD
    assert D.mentions_part([text], "SS14") is True
    assert D.mentions_part([text], "SS19") is False


def test_an_ordering_code_segment_at_the_end_is_not_part_of_the_name():
    text = "ESP32-S3-WROOM-1 ESP32-S3-WROOM-1U Datasheet" + PAD
    assert D.mentions_part([text], "ESP32-S3-WROOM-1-N8R8") is True
    assert D.mentions_part([text], "ESP32-S3-N8R8") is True
    assert D.mentions_part(["ESP32-C3-MINI-1 Datasheet" + PAD], "ESP32-S3-N8R8") is False


def test_case_and_ordering_tails_change_nothing():
    for mpn, text in POS + NEG:
        want = D.mentions_part([text + PAD], mpn)
        for variant in (mpn.lower(), mpn + "#PBF", mpn + "/NOPB", " " + mpn + " "):
            assert D.mentions_part([text + PAD], variant) is want, (mpn, variant)
        assert D.mentions_part([text.lower() + PAD], mpn) is want, (mpn, "lower-case text")


def test_the_mpn_itself_always_names_the_part():
    for mpn, text in POS + NEG:
        if D.mentions_part([text + PAD], mpn) is None:
            continue
        assert D.mentions_part([f"Ordering information: {mpn} " + text + PAD], mpn) is True, mpn
        assert D.mentions_part(["", None, text + PAD, f"page 5: {mpn}"], mpn) is True, mpn


def test_none_when_it_cannot_be_told():
    assert D.mentions_part(["STM32F103"], "STM32F103C8T6") is None          # too little text (scanned PDF)
    assert D.mentions_part([], "STM32F103C8T6") is None
    assert D.mentions_part(None, "STM32F103C8T6") is None
    assert D.mentions_part(["x" * 100], "") is None
    assert D.mentions_part(["x" * 100], None) is None
    assert D.mentions_part(["x" * 100], "ABCDEF") is None                    # no digits: no numeric core
    assert D.mentions_part(["x" * 100], "R1") is None                        # too short


def test_odd_input_never_raises():
    rng = random.Random(1022)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/#+.,_ ()xX\u00b5\u03a9\u5fb7\t\n[]*?^$|"
    for _ in range(3000):
        mpn = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300)))
        assert D.mentions_part([text], mpn) in (True, False, None)
