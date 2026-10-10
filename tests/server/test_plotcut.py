"""Plot Cut API tests — mocked devices, no real USB."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.plotcut import (
    MAX_JOB_BYTES,
    PlotCutError,
    PlotCutService,
    create_router,
)


class FakeForwarder:
    def __init__(self):
        self.states: dict[str, SimpleNamespace] = {}
        self.bridges: dict = {}
        self.busy: set[str] = set()
        self.paused: list[str] = []
        self.resumed: list[str] = []

    def find_state(self, bus_id: str, vendor_id: str = "", product_id: str = ""):
        if bus_id and bus_id in self.states:
            return self.states[bus_id]
        for state in self.states.values():
            if vendor_id and state.vendor_id == vendor_id and state.product_id == product_id:
                return state
        return None

    def get_usb_bridge(self, bus_id: str):
        return self.bridges.get(bus_id)

    def mark_plotcut_busy(self, bus_id: str, busy: bool) -> None:
        if busy:
            self.busy.add(bus_id)
        else:
            self.busy.discard(bus_id)

    def is_plotcut_busy(self, bus_id: str) -> bool:
        return bus_id in self.busy

    async def pause_usbip_for_plotcut(self, bus_id: str) -> bool:
        self.paused.append(bus_id)
        state = self.states.get(bus_id)
        if state:
            state.usbip_shared = False
        return True

    async def resume_usbip_after_plotcut(self, bus_id: str) -> None:
        self.resumed.append(bus_id)
        state = self.states.get(bus_id)
        if state:
            state.usbip_shared = True


class FakeBridge:
    def __init__(self, client: bool = False):
        self._client = client
        self.written: list[bytes] = []

    @property
    def has_client(self) -> bool:
        return self._client

    def write_raw(self, data: bytes, chunk_size: int = 4096, timeout: int = 30000) -> int:
        self.written.append(data)
        return len(data)


def _usb(vid, pid, bus_id, product, device_class="Cutting Plotter"):
    return SimpleNamespace(
        vendor_id=vid,
        product_id=pid,
        bus_id=bus_id,
        product=product,
        device_class=device_class,
        custom_name=None,
    )


@pytest.fixture
def tmp_cfg(tmp_path):
    return tmp_path


@pytest.fixture
def service(tmp_cfg):
    fwd = FakeForwarder()
    usb_list = [
        _usb("0a50", "0001", "1-18", "Mimaki CG-SR Cutting Plotter"),
        _usb("1a86", "7523", "1-3", "CH340 Serial Cutter (Vevor / Generic)"),
    ]

    async def list_usb():
        return list(usb_list)

    fwd.states["1-18"] = SimpleNamespace(
        bus_id="1-18",
        vendor_id="0a50",
        product_id="0001",
        usbip_shared=False,
        usbip_busid=None,
        serial_port=7580,
        serial_dev="usb-bridge:0a50:0001",
        attach_mode="bridge",
    )
    fwd.states["1-3"] = SimpleNamespace(
        bus_id="1-3",
        vendor_id="1a86",
        product_id="7523",
        usbip_shared=False,
        usbip_busid=None,
        serial_port=7581,
        serial_dev="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
        attach_mode="",
    )
    fwd.bridges["1-18"] = FakeBridge()

    svc = PlotCutService(fwd, list_usb, data_dir=tmp_cfg, usbip_port=7575)
    svc._usb_list = usb_list
    svc.forwarder = fwd
    svc.writes: list[tuple] = []

    def record_socat(host, port, data):
        svc.writes.append(("socat", host, port, data))
        return len(data)

    def record_pyusb(vid, pid, data):
        svc.writes.append(("pyusb", vid, pid, data))
        return len(data)

    svc.write_socat = record_socat
    svc.write_pyusb = record_pyusb
    return svc


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(create_router(service))
    return TestClient(app)


def test_list_devices_stable_ids(client):
    r = client.get("/api/plotcut/devices")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()["devices"]]
    assert "mimaki-cg60sr" in ids
    assert "vevor" in ids
    mimaki = next(d for d in r.json()["devices"] if d["id"] == "mimaki-cg60sr")
    assert mimaki["kind"] == "usb-vendor"
    assert mimaki["available"] is True
    assert mimaki["attached_to"] is None


def test_job_writes_unaltered_bytes(client, service):
    payload = b"IN;PU0,0;PD400,0;PU0,0;"
    r = client.post(
        "/api/plotcut/devices/mimaki-cg60sr/job",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "bytes": 23}
    bridge = service.forwarder.bridges["1-18"]
    assert bridge.written == [payload]


def test_vevor_job_uses_socat(client, service):
    payload = b"IN;PU0,0;PD400,0;PU0,0;"
    r = client.post(
        "/api/plotcut/devices/vevor/job",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    assert r.json()["bytes"] == 23
    assert service.writes[0][0] == "socat"
    assert service.writes[0][2] == 7581
    assert service.writes[0][3] == payload


def test_unknown_id_404(client):
    r = client.post(
        "/api/plotcut/devices/does-not-exist/job",
        content=b"IN;",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 404
    assert "Unbekanntes" in r.json()["detail"]


def test_occupied_usbip_409(client, service, monkeypatch):
    state = service.forwarder.states["1-18"]
    state.usbip_shared = True
    state.usbip_busid = "1-1.3.1"

    monkeypatch.setattr("api.plotcut.read_usbip_status", lambda busid: 1)
    monkeypatch.setattr("api.plotcut.usbip_peer_label", lambda port=7575: "PC-BUERO")

    listed = client.get("/api/plotcut/devices").json()["devices"]
    mimaki = next(d for d in listed if d["id"] == "mimaki-cg60sr")
    assert mimaki["available"] is False
    assert mimaki["attached_to"] == "PC-BUERO"

    r = client.post(
        "/api/plotcut/devices/mimaki-cg60sr/job",
        content=b"IN;",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 409
    assert "USB/IP" in r.json()["detail"]
    assert "PC-BUERO" in r.json()["detail"]
    assert service.forwarder.paused == []


def test_concurrent_job_409(service):
    started = threading.Event()
    release = threading.Event()

    orig = service.forwarder.bridges["1-18"].write_raw

    def blocking_write(data, chunk_size=4096, timeout=30000):
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test lock was not released")
        return orig(data)

    service.forwarder.bridges["1-18"].write_raw = blocking_write

    async def run():
        t1 = asyncio.create_task(service.submit_job("mimaki-cg60sr", b"AAAA"))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        assert started.is_set()
        with pytest.raises(PlotCutError) as exc:
            await service.submit_job("mimaki-cg60sr", b"BBBB")
        assert exc.value.status_code == 409
        release.set()
        result = await t1
        assert result.ok is True
        assert result.bytes == 4

    asyncio.run(run())


def test_body_limit_413(client):
    r = client.post(
        "/api/plotcut/devices/mimaki-cg60sr/job",
        content=b"x" * (MAX_JOB_BYTES + 1),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 413
    assert "50 MB" in r.json()["detail"]


def test_auto_id_for_unknown_plotter(tmp_cfg):
    extra = _usb("0a5f", "0005", "1-9", "Graphtec CE6000", "Cutting Plotter")

    async def list_usb():
        return [extra]

    svc = PlotCutService(FakeForwarder(), list_usb, data_dir=tmp_cfg)
    devices = asyncio.run(svc.list_devices())
    ids = [d.id for d in devices]
    assert "plotter-0a5f-0005" in ids
    assert "mimaki-cg60sr" in ids  # still listed from config, not plugged in
    missing = next(d for d in devices if d.id == "mimaki-cg60sr")
    assert missing.available is False
