#!/usr/bin/env python3
"""Capture six bench channels through the Saleae Logic 2 Automation API.

Requires logic2-automation. Creates a fresh directory, retains raw binary and
optional .sal, and records acquisition settings and wall-clock coverage.
"""
import argparse
import dataclasses
import datetime
import json
import pathlib
import time
import traceback

from saleae import automation


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def capture_once(output, seconds=10.0, rate=500_000_000, save_sal=True,
                 device_id="6753424D9C96677D", label="", ready_file=None, stop_file=None):
    output = pathlib.Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = dict(schema_version=1, label=label, status="starting", requested_seconds=seconds,
                    sample_rate_hz=rate, digital_channels=list(range(6)),
                    analog_channels=[], glitch_filters=[], digital_voltage_setting=3.3,
                    nominal_threshold_volts=1.65, device_id=device_id,
                    pin_order=["P8_8", "P8_10", "P8_12", "P8_14", "P8_16", "P8_18"],
                    started_utc=utc())
    metadata["mode"] = "manual_until_stop_file" if stop_file else "timed"
    def record():
        temporary=output/"capture.json.tmp"
        temporary.write_text(json.dumps(metadata, indent=2, default=str)+"\n")
        temporary.replace(output/"capture.json")
    record()
    start = time.monotonic()
    try:
        with automation.Manager.connect(port=10430, connect_timeout_seconds=10) as manager:
            metadata["app"] = dataclasses.asdict(manager.get_app_info())
            devices = manager.get_devices()
            if not any(d.device_id == device_id and not d.is_simulation for d in devices):
                raise RuntimeError("Expected physical Saleae is not connected")
            settings = automation.LogicDeviceConfiguration(
                enabled_digital_channels=list(range(6)), enabled_analog_channels=[],
                digital_sample_rate=rate, digital_threshold_volts=3.3, glitch_filters=[])
            mode = automation.CaptureConfiguration(
                buffer_size_megabytes=2048,
                capture_mode=(automation.ManualCaptureMode() if stop_file else
                              automation.TimedCaptureMode(duration_seconds=seconds)))
            with manager.start_capture(device_configuration=settings, device_id=device_id,
                                       capture_configuration=mode) as cap:
                metadata["capture_started_utc"] = utc()
                metadata["status"] = "capturing"
                record()
                if ready_file:
                    pathlib.Path(ready_file).write_text(utc()+"\n")
                if stop_file:
                    limit=time.monotonic()+seconds
                    while not pathlib.Path(stop_file).exists() and time.monotonic()<limit:
                        time.sleep(0.02)
                    metadata["stop_reason"] = "stop_file" if pathlib.Path(stop_file).exists() else "time_limit"
                    cap.stop()
                else:
                    cap.wait()
                metadata["capture_finished_utc"] = utc()
                metadata["status"] = "exporting"
                record()
                export_errors=[]
                if save_sal:
                    try:
                        cap.save_capture(str(output / "waveform.sal"))
                    except Exception as exc:
                        export_errors.append("native save: "+repr(exc))
                try:
                    cap.export_raw_data_binary(str(output), digital_channels=list(range(6)))
                except Exception as exc:
                    export_errors.append("raw export: "+repr(exc))
                if export_errors:
                    metadata["export_errors"]=export_errors
                    raise RuntimeError("; ".join(export_errors))
                if stop_file and metadata["stop_reason"] != "stop_file":
                    raise RuntimeError("manual capture reached time limit before stop file")
            metadata["status"] = "complete"
    except BaseException as exc:
        metadata["status"] = "error"
        metadata["error"] = repr(exc)
        metadata["traceback"] = traceback.format_exc()
        raise
    finally:
        metadata["finished_utc"] = utc()
        metadata["wall_seconds"] = time.monotonic()-start
        metadata["files"] = {p.name:p.stat().st_size for p in output.iterdir() if p.is_file() and p.name != "capture.json"}
        record()
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--rate", type=int, default=500_000_000)
    parser.add_argument("--device-id", default="6753424D9C96677D")
    parser.add_argument("--label", default="")
    parser.add_argument("--no-sal", action="store_true")
    parser.add_argument("--ready-file")
    parser.add_argument("--stop-file", help="manual capture: stop when this file exists; --seconds is a safety cap")
    args = parser.parse_args()
    if not 0 < args.seconds <= 300:
        parser.error("capture duration must be >0 and <=300 seconds")
    print(json.dumps(capture_once(args.output, args.seconds, args.rate, not args.no_sal,
                                 args.device_id, args.label, args.ready_file, args.stop_file), default=str), flush=True)
