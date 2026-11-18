# Spike 1, follow-up on Fargate: can a process confine its own writes with
# Landlock (ABI 2) and no namespaces? Writes are allowed only under /tmp/work.
import ctypes, json, os, struct
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def sc(*a):
    r = libc.syscall(*a)
    if r < 0:
        e = ctypes.get_errno(); raise OSError(e, os.strerror(e))
    return r
# Every write-like access right in ABI 2: write, remove, make, refer.
W = 2 | 16 | 32 | 64 | 128 | 256 | 512 | 1024 | 2048 | 4096 | 8192
os.makedirs("/tmp/work", exist_ok=True)
pid = os.fork()
if pid == 0:
    try:
        fd = sc(444, ctypes.create_string_buffer(struct.pack("Q", W), 8), ctypes.c_size_t(8), ctypes.c_uint32(0))
        d = os.open("/tmp/work", os.O_PATH)
        sc(445, fd, 1, ctypes.create_string_buffer(struct.pack("=Qi", W, d), 12), ctypes.c_uint32(0))
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
        sc(446, fd, ctypes.c_uint32(0))
        out = {}
        for p in ["/tmp/work/allowed", "/tmp/outside", "/etc/outside"]:
            try:
                open(p, "w").write("x"); out[p] = "written"
            except OSError as e:
                out[p] = "errno %d (%s)" % (e.errno, e.strerror)
        out["read /etc/hostname"] = "ok" if open("/etc/hostname").read() else "empty"
        print(json.dumps({"landlock_sandbox": out}), flush=True)
        os._exit(0)
    except OSError as e:
        print(json.dumps({"landlock_sandbox": "failed: %s" % e}), flush=True); os._exit(1)
os.waitpid(pid, 0)
