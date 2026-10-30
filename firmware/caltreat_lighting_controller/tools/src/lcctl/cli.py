"""lcctl: command line for the lighting controller boards."""

from __future__ import annotations

import json
import shlex
from contextlib import contextmanager

import typer

from . import image as fwimage
from . import ota as fwota
from . import regmap as rm
from .client import Board, PinConfig
from .discover import discover as discover_boards

app = typer.Typer(no_args_is_help=True, help="Control CalTreat lighting controller boards over Modbus TCP.")


def _board(host: str | None) -> Board:
    if host:
        return Board(host)
    found = discover_boards()
    if len(found) != 1:
        raise typer.BadParameter(f"found {len(found)} boards; pass --host")
    return Board(found[0]["ip"])


HostOpt = typer.Option(None, "--host", "-H", help="Board IP (default: the only discovered board)")

# Set while `lcctl shell` runs: commands reuse its connection
_session: Board | None = None


@contextmanager
def _open(host: str | None):
    if _session is not None:
        yield _session
        return
    with _board(host) as b:
        yield b


@app.command()
def discover() -> None:
    """List boards found through the DHCP lease files."""
    for b in discover_boards():
        typer.echo(f"{b['ip']:15}  uid {b['uid']}  mac {b['lease_mac']}  fw {b['fw_version']}  partition {b['partition']}")


@app.command()
def info(host: str | None = HostOpt) -> None:
    """Show board status and identification."""
    with _open(host) as b:
        data = b.info()
        data["device_id"] = {f"0x{k:02x}": v for k, v in b.device_id().items()}
        data["heartbeat_ms"] = b.heartbeat_ms
    typer.echo(json.dumps(data, indent=2))


@app.command()
def pins(host: str | None = HostOpt) -> None:
    """Show every header pin's configuration and level."""
    with _open(host) as b:
        levels = b.read_inputs()
        for gpio in rm.HEADER_PINS:
            p = b.pin(gpio)
            c = p["config"]
            func = {v: k for k, v in rm.FUNC_NAMES.items()}[c.func]
            typer.echo(f"GP{gpio:<2} {func:6} pull {c.pull} drive {c.drive} flags 0x{c.flags:02x} "
                       f"duty {c.duty:5} freq {c.freq_hz:6} level {int(levels[gpio])} edges {p['edges']}")


@app.command("set")
def set_pin(
    gpio: int,
    func: str = typer.Argument(..., help="off | input | output | pwm | adc"),
    pull: str = typer.Option("none", help="none | up | down | keep"),
    level: int = typer.Option(0, help="output level"),
    duty: int = typer.Option(0, help="PWM duty 0-65535"),
    freq: int = typer.Option(200, help="PWM frequency (Hz)"),
    host: str | None = HostOpt,
) -> None:
    """Configure a pin."""
    flags = rm.FLAG_SCHMITT | (rm.FLAG_LEVEL_HIGH if level else 0)
    cfg = PinConfig(rm.FUNC_NAMES[func], rm.PULL_NAMES[pull], rm.DRIVE_4MA, flags, duty, freq)
    with _open(host) as b:
        b.configure(gpio, cfg)


@app.command()
def out(gpio: int, level: int, host: str | None = HostOpt) -> None:
    """Drive an OUTPUT pin high (1) or low (0)."""
    with _open(host) as b:
        b.write_output(gpio, bool(level))


@app.command()
def pwm(gpio: int, duty: int, fade_ms: int = typer.Option(None), host: str | None = HostOpt) -> None:
    """Set a PWM pin's duty (0-65535), optionally fading."""
    with _open(host) as b:
        b.set_pwm(gpio, duty, fade_ms)


@app.command()
def adc(host: str | None = HostOpt) -> None:
    """Read the ADC inputs and temperature."""
    with _open(host) as b:
        typer.echo(json.dumps(b.adc(), indent=2))


@app.command()
def heartbeat(ms: int = typer.Argument(None), host: str | None = HostOpt) -> None:
    """Show or set the heartbeat timeout (ms, 0 = off). Use `save` to persist."""
    with _open(host) as b:
        if ms is not None:
            b.heartbeat_ms = ms
        typer.echo(b.heartbeat_ms)


@app.command()
def save(host: str | None = HostOpt) -> None:
    """Persist heartbeat and failsafe defaults to flash."""
    with _open(host) as b:
        b.save()


@app.command()
def reboot(bootsel: bool = typer.Option(False, help="reboot into USB BOOTSEL"), host: str | None = HostOpt) -> None:
    """Reboot the board."""
    b = _board(host).connect()
    b.reboot_bootsel() if bootsel else b.reboot()


@app.command()
def ota(
    firmware: str = typer.Argument(".pio/build/app/firmware.bin", help="sealed firmware.bin"),
    allow_downgrade: bool = typer.Option(False, help="allow installing an older version (revert)"),
    host: str | None = HostOpt,
) -> None:
    """Update the firmware over the network."""
    img = fwimage.load(firmware)
    b = _board(host)
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = done * 100 // total
        if pct // 10 != last[0]:
            last[0] = pct // 10
            typer.echo(f"  {pct:3d}%")

    try:
        fwota.update(b.host, img, allow_downgrade=allow_downgrade, progress=progress, log=typer.echo)
    except fwota.UpdateError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def shell(host: str | None = HostOpt) -> None:
    """Interactive session on one connection.

    Each lcctl command opens its own connection, and the board returns its pins
    to their failsafe defaults when a connection closes. In the shell, pin
    changes last until you quit (the heartbeat is paused meanwhile).
    """
    global _session
    b = _board(host).connect()
    saved_heartbeat = b.heartbeat_ms
    b.heartbeat_ms = 0
    _session = b
    typer.echo(f"connected to {b.host}; heartbeat paused. Commands as on the command line, 'quit' to leave.")
    try:
        while True:
            try:
                line = input("lcctl> ")
            except EOFError:
                break
            args = shlex.split(line)
            if not args:
                continue
            if args[0] in ("quit", "exit"):
                break
            if args[0] in ("shell", "ota", "reboot", "discover"):
                typer.echo(f"'{args[0]}' is not available in the shell")
                continue
            try:
                app(args, standalone_mode=False)
            except (typer.Exit, typer.Abort, SystemExit):
                pass
            except Exception as e:  # noqa: BLE001 - usage errors included; keep the session alive
                typer.echo(f"error: {e}")
    finally:
        _session = None
        try:
            b.heartbeat_ms = saved_heartbeat
        finally:
            b.close()


if __name__ == "__main__":
    app()
