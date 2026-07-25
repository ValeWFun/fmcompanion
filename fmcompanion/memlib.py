"""Rein lesender Speicherzugriff auf fm.exe (64-Bit): andocken, Heap-Regionen
aufzaehlen, Bytes lesen."""
import ctypes
from ctypes import wintypes
import pymem

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02
PAGE_GUARD = 0x100


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wintypes.DWORD),
        ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__a2", wintypes.DWORD),
    ]


def attach(name="fm.exe"):
    """Dockt an den Prozess an. Wirft pymem.exception.ProcessNotFound, wenn fehlt.
    exact_match=True: sonst matcht pymem als Substring einen falschen Prozess."""
    pm = pymem.Pymem(name, exact_match=True, ignore_case=True)
    base = pymem.process.module_from_name(pm.process_handle, name).lpBaseOfDll
    return pm, base


def iter_regions(pm, private_rw_only=True):
    VQ = ctypes.windll.kernel32.VirtualQueryEx
    VQ.restype = ctypes.c_size_t
    VQ.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong, ctypes.POINTER(MBI), ctypes.c_size_t]
    mbi = MBI()
    addr = 0
    while addr < 0x7FFFFFFFFFFF:
        if VQ(pm.process_handle, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base, size = mbi.BaseAddress, mbi.RegionSize
        nxt = base + size
        if mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD):
            if private_rw_only:
                ok = (mbi.Type == MEM_PRIVATE and mbi.Protect == PAGE_READWRITE)
            else:
                ok = mbi.Protect in (PAGE_READWRITE, PAGE_READONLY)
            if ok and size > 0:
                yield base, size
        if nxt <= addr:
            break
        addr = nxt


def read(pm, addr, size):
    try:
        return pm.read_bytes(addr, size)
    except Exception:
        return None


def read_regions(pm, private_rw_only=True, max_chunk=8 * 1024 * 1024):
    """Generator ueber (base, data-bytes) aller Zielregionen, in Chunks."""
    for base, size in iter_regions(pm, private_rw_only):
        off = 0
        while off < size:
            n = min(max_chunk, size - off)
            data = read(pm, base + off, n)
            if data:
                yield base + off, data
            off += n
