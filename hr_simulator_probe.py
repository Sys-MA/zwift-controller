"""Synthetic BLE heart rate diagnostic for the device pairing screen only.

Do not use during rides, races or recorded activities. No Fitbit data is used.
"""
import argparse
import asyncio
import sys
import time
from uuid import UUID

SERVICE = UUID("0000180d-0000-1000-8000-00805f9b34fb")
MEASUREMENT = UUID("00002a37-0000-1000-8000-00805f9b34fb")
TEST_VALUES = (80, 100, 120, 140)

async def run(seconds):
    from winrt.windows.devices.bluetooth import BluetoothAdapter, BluetoothError
    from winrt.windows.devices.bluetooth.genericattributeprofile import (
        GattServiceProvider, GattServiceProviderAdvertisingParameters,
        GattLocalCharacteristicParameters, GattCharacteristicProperties,
        GattProtectionLevel,
    )
    from winrt.windows.storage.streams import DataWriter

    adapter = await BluetoothAdapter.get_default_async()
    if adapter is None:
        print("No Bluetooth adapter found.")
        return
    print("BLE central supported:", adapter.is_central_role_supported)
    print("BLE peripheral supported:", adapter.is_peripheral_role_supported)
    if not adapter.is_peripheral_role_supported:
        print("This adapter cannot advertise a local GATT server.")
        return

    result = await GattServiceProvider.create_async(SERVICE)
    if result.error != BluetoothError.SUCCESS:
        print("Service creation failed:", result.error)
        return
    provider = result.service_provider
    params = GattLocalCharacteristicParameters()
    params.characteristic_properties = GattCharacteristicProperties.NOTIFY
    params.read_protection_level = GattProtectionLevel.PLAIN
    params.write_protection_level = GattProtectionLevel.PLAIN
    params.user_description = "SIMULATED HR - TEST ONLY"
    result = await provider.service.create_characteristic_async(MEASUREMENT, params)
    if result.error != BluetoothError.SUCCESS:
        print("Characteristic creation failed:", result.error)
        return
    measurement = result.characteristic

    # Optional standard HRS Body Sensor Location: chest (1).
    location_params = GattLocalCharacteristicParameters()
    location_params.characteristic_properties = GattCharacteristicProperties.READ
    location_params.read_protection_level = GattProtectionLevel.PLAIN
    writer = DataWriter()
    try:
        writer.write_byte(1)
        location_params.static_value = writer.detach_buffer()
        location_result = await provider.service.create_characteristic_async(
            UUID("00002a38-0000-1000-8000-00805f9b34fb"), location_params
        )
    finally:
        writer.close()
    if location_result.error != BluetoothError.SUCCESS:
        print("Body Sensor Location creation failed:", location_result.error)
        return
    location = location_result.characteristic
    print("Heart Rate Measurement (2A37) + Body Sensor Location (2A38) ready.")

    advertising = GattServiceProviderAdvertisingParameters()
    advertising.is_discoverable = True
    advertising.is_connectable = True
    try:
        provider.start_advertising(advertising)
        print("Synthetic HR test ONLY. Open MyWhoosh device connection screen; do not start a ride.")
        print("The advertised name may be your Windows computer name.")
        deadline = time.monotonic() + seconds
        started = time.monotonic()
        while time.monotonic() < deadline:
            bpm = TEST_VALUES[int((time.monotonic() - started) / 5) % len(TEST_VALUES)]
            count = len(measurement.subscribed_clients)
            status = provider.advertisement_status
            if count:
                writer = DataWriter()
                writer.write_byte(0)  # 8-bit BPM; no optional fields
                writer.write_byte(bpm)
                buffer = writer.detach_buffer()
                try:
                    results = await asyncio.wait_for(measurement.notify_value_async(buffer), 5)
                    print(f"Advertising={status} clients={count} TEST BPM={bpm} notify={[str(x.status) for x in results]}", flush=True)
                finally:
                    writer.close()
            else:
                print(f"Advertising={status} clients=0 (waiting for subscription)", flush=True)
            await asyncio.sleep(1)
    finally:
        provider.stop_advertising()
        print("Test stopped. Bluetooth service no longer advertised.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=90, choices=range(10, 181), metavar="10-180")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("This diagnostic requires Windows.")
        return
    try:
        asyncio.run(run(args.seconds))
    except KeyboardInterrupt:
        print("Stopped by user.")
    except ImportError as exc:
        print("WinRT Python package missing:", exc)
    except Exception as exc:
        print(f"Diagnostic failed: {type(exc).__name__}: {exc}")

if __name__ == "__main__":
    main()
