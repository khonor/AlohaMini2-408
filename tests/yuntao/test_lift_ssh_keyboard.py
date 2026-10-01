#!/usr/bin/env python3
"""`lift_ssh.py` 的键盘链路测试 —— 在伪终端(pty)里模拟按住/松开/急停/退出。

为什么需要它：和 `wheels_ssh.py` 一样，`lift_ssh.py` 依赖「终端自动重复」来模拟
按住，这条逻辑没法靠肉眼确认。另外升降轴的几个特有行为也值得自动验证：

  * 上升/下降的速度**符号**是否正确（`dir_sign=-1`，所以上升下发的是负速度）
  * 松开后是否归零
  * 方向键（↑/↓ 的 ESC 序列）能否被识别
  * **Space 急停后是否锁存**：必须真正松开方向键才能再次驱动

全程 `--dry-run`，不连接也不驱动电机，可随时反复运行。

用法：
    python tests/yuntao/test_lift_ssh_keyboard.py
    pytest tests/yuntao/test_lift_ssh_keyboard.py -sv
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "lift_ssh.py")

REPEAT_INTERVAL_S = 0.08  # 模拟终端自动重复的间隔
RELEASE_WAIT_S = 1.0  # 需长于 --initial-hold-ms(700ms)

# dry-run 的 HUD 形如：
#   [dry] 按键=up             速度= +600  下发 Goal_Velocity= -600   高度=  +0.00mm  行程=  +0.00mm
# 注意 `:+5d` 会补前导空格，所以等号后面可能有空格。
STATUS_LINE_RE = re.compile(
    r"\[dry\] 按键=(?P<keys>\S+)\s+"
    r"速度=\s*(?P<v>[+-]?\d+)\s+"
    r"下发 Goal_Velocity=\s*(?P<raw>[+-]?\d+)"
)

UP_ARROW = "\x1b[A"
DOWN_ARROW = "\x1b[B"


class PtySession:
    """把被测脚本跑在 pty 里，可精确控制送键时序并抓取输出。"""

    def __init__(self, argv: list[str]):
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        self.raw = ""

    def _drain(self, timeout: float = 0.1) -> None:
        while True:
            ready, _, _ = select.select([self.master], [], [], timeout)
            if not ready:
                return
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            if not data:
                return
            self.raw += data.decode(errors="ignore")

    def send(self, keys: str) -> None:
        os.write(self.master, keys.encode())

    def hold(self, key: str, duration_s: float) -> None:
        """模拟「按住」：按自动重复的节奏连续发送同一个键。"""
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self.send(key)
            self._drain(0.01)
            time.sleep(REPEAT_INTERVAL_S)
        self._drain(0.05)

    def idle(self, duration_s: float) -> None:
        """不发任何键（模拟松开）。"""
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            self._drain(0.05)
            time.sleep(0.02)

    def wait_for(self, needle: str, timeout_s: float = 60.0) -> bool:
        """等待输出中出现某段文字（脚本导入 lerobot+torch 要 ~5.4s，不能盲等）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if needle in self.raw.replace("\r", "\n"):
                return True
            self._drain(0.1)
        return False

    def wait_exit(self, timeout_s: float = 5.0) -> int | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self._drain(0.05)
                return self.proc.returncode
            self._drain(0.05)
            time.sleep(0.02)
        return None

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=3)
        try:
            os.close(self.master)
        except OSError:
            pass

    # ---- 解析 ----
    def statuses(self) -> list[dict[str, int | str]]:
        text = self.raw.replace("\r", "\n")
        out: list[dict[str, int | str]] = []
        for m in STATUS_LINE_RE.finditer(text):
            out.append(
                {
                    "keys": m.group("keys"),
                    "v": int(m.group("v")),
                    "raw": int(m.group("raw")),
                }
            )
        return out

    def last(self) -> dict[str, int | str] | None:
        s = self.statuses()
        return s[-1] if s else None


def _start() -> PtySession:
    sess = PtySession([sys.executable, SCRIPT, "--dry-run"])
    # 树莓派上首次 import torch 偶发很慢，给足余量
    assert sess.wait_for("按键：", timeout_s=180), (
        f"脚本未在 180s 内启动完成。已收到的输出:\n{sess.raw[-800:]}"
    )
    return sess


def test_lift_up_down_signs_and_release() -> None:
    """上升/下降的速度符号正确，松开后归零。"""
    sess = _start()
    try:
        # 1) 按住 K（上升）
        sess.hold("k", 0.8)
        up = [s for s in sess.statuses() if s["keys"] == "up"]
        assert up, f"未识别到 up。原始输出:\n{sess.raw[-600:]}"
        assert up[-1]["v"] > 0, f"上升速度应为正，实际: {up[-1]}"
        assert up[-1]["raw"] < 0, (
            f"dir_sign=-1，上升应下发负的 Goal_Velocity，实际: {up[-1]}"
        )

        # 2) 松开 -> 归零
        sess.idle(RELEASE_WAIT_S)
        after = sess.statuses()[-1]
        assert after["v"] == 0, f"松开后速度未归零: {after}"
        assert after["raw"] == 0, f"松开后下发速度未归零: {after}"

        # 3) 按住 J（下降）
        sess.hold("j", 0.8)
        down = [s for s in sess.statuses() if s["keys"] == "down"]
        assert down, f"未识别到 down。全部按键: {[s['keys'] for s in sess.statuses()][-8:]}"
        assert down[-1]["v"] < 0, f"下降速度应为负，实际: {down[-1]}"
        assert down[-1]["raw"] > 0, f"下降应下发正的 Goal_Velocity，实际: {down[-1]}"

        # 4) 退出
        sess.idle(RELEASE_WAIT_S)
        sess.send("q")
        rc = sess.wait_exit(timeout_s=5)
        assert rc == 0, f"按 q 后进程应正常退出，实际 returncode={rc}"
    finally:
        sess.cleanup()


def test_arrow_keys_are_decoded() -> None:
    """↑ / ↓ 的 ESC 序列应被识别为 up / down。"""
    sess = _start()
    try:
        sess.hold(UP_ARROW, 0.8)
        up = [s for s in sess.statuses() if s["keys"] == "up"]
        assert up, f"↑ 未被识别为 up。原始输出:\n{sess.raw[-600:]}"
        assert up[-1]["v"] > 0

        sess.idle(RELEASE_WAIT_S)
        sess.hold(DOWN_ARROW, 0.8)
        down = [s for s in sess.statuses() if s["keys"] == "down"]
        assert down, f"↓ 未被识别为 down。原始输出:\n{sess.raw[-600:]}"
        assert down[-1]["v"] < 0

        sess.idle(RELEASE_WAIT_S)
        sess.send("q")
        assert sess.wait_exit(timeout_s=5) == 0
    finally:
        sess.cleanup()


def test_space_latches_until_release() -> None:
    """Space 急停后应锁存：一直按住不放则始终为 0，真正松开后才能重新驱动。"""
    sess = _start()
    try:
        # 先按住上升，确认能动
        sess.hold("k", 0.8)
        assert sess.last()["v"] > 0, "初始应能驱动"

        # 按 Space 急停，但**继续按住** K
        sess.send(" ")
        sess.hold("k", 0.8)
        latched = [s for s in sess.statuses() if s["keys"] == "up" and s["v"] == 0]
        assert latched, (
            f"Space 之后仍被驱动，锁存失效。最近状态: {sess.statuses()[-5:]}"
        )
        assert sess.last()["v"] == 0, f"急停后应为 0，实际: {sess.last()}"

        # 真正松开，等锁存解除
        sess.idle(RELEASE_WAIT_S)

        # 再按住 -> 应恢复
        sess.hold("k", 0.8)
        assert sess.last()["v"] > 0, f"松开后应能重新驱动，实际: {sess.last()}"

        sess.idle(RELEASE_WAIT_S)
        sess.send("q")
        assert sess.wait_exit(timeout_s=5) == 0
    finally:
        sess.cleanup()


def test_no_tty_at_all_is_rejected() -> None:
    """stdin 不是终端、且连控制终端都没有时，应明确报错而不是静默什么都不做。

    注意 `start_new_session=True`：脚本对被管道接管的 stdin 会自动退回读
    `/dev/tty`，所以必须真正脱离控制终端（setsid），才能走到报错那条分支。
    """
    p = subprocess.run(
        [sys.executable, SCRIPT, "--dry-run"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,
    )
    out = p.stdout + p.stderr
    assert "读不到终端" in out, f"未给出无终端提示。输出:\n{out[-600:]}"


def test_dev_tty_fallback_when_stdin_piped() -> None:
    """模拟 `conda run`：stdin 被接管，但仍有控制终端 -> 应退回读 /dev/tty 并正常工作。

    这是用户实际踩到的坑（`conda run -n <env> python ...` 读不到按键）。
    用 `pty.fork()` 让 pty 成为**控制终端**，再把 fd 0 换成 /dev/null，
    这样 `sys.stdin.isatty()` 为 False 而 `/dev/tty` 仍可打开。
    """
    pid, master = pty.fork()
    if pid == 0:  # 子进程
        fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(fd, 0)
        os.close(fd)
        os.execv(sys.executable, [sys.executable, SCRIPT, "--dry-run"])
        os._exit(127)

    raw = ""
    warn_seen = False
    listener_ready = False
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                raw += data.decode(errors="ignore")
            if "stdin 不是终端" in raw:
                warn_seen = True
            # ⚠️ 必须等到监听线程真的起来再送键。
            # [WARN] 是在 listener.start() **之前**打印的，那时终端还是 canonical
            # 模式，按键只会被回显并留在行缓冲里，直到收到换行才交给 read()。
            # 「按键：」这行是在 listener.start() 之后打印的，用它当就绪信号。
            if warn_seen and "按键：" in raw:
                listener_ready = True
                break
        assert warn_seen, f"未触发 /dev/tty 回退。输出:\n{raw[-800:]}"
        assert listener_ready, f"未等到监听线程就绪。输出:\n{raw[-800:]}"

        # 通过 /dev/tty 送一个按键，验证回退后键盘真的能用
        os.write(master, b"k")
        time.sleep(0.8)
        os.write(master, b"q")

        deadline = time.monotonic() + 10
        status = None
        while time.monotonic() < deadline:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    d = os.read(master, 4096)
                except OSError:
                    d = b""
                if d:
                    raw += d.decode(errors="ignore")
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                break
        assert status is not None, f"按 q 后进程未退出。输出:\n{raw[-800:]}"
        assert os.waitstatus_to_exitcode(status) == 0, f"退出码异常: {status}"
        assert "速度= +600" in raw, f"回退后按键未生效。输出:\n{raw[-800:]}"
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


if __name__ == "__main__":
    t0 = time.monotonic()
    cases = [
        test_lift_up_down_signs_and_release,
        test_arrow_keys_are_decoded,
        test_space_latches_until_release,
        test_no_tty_at_all_is_rejected,
        test_dev_tty_fallback_when_stdin_piped,
    ]
    failed = 0
    for case in cases:
        try:
            case()
            print(f"✅ {case.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"❌ {case.__name__}: {e}")
    print(f"\n{'全部通过' if not failed else f'{failed} 个失败'}，耗时 {time.monotonic() - t0:.1f}s")
    sys.exit(1 if failed else 0)
